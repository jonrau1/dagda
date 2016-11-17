import docker
import datetime
import json
import re
from cveDB.mongodb_driver import MongoDbDriver
from util.check_docker_image_cli_parser import CheckDockerImageCLIParser
import sys


# Gets installed software from docker image
def get_soft_from_docker_image(cli, image_name):
    # Start container
    container = cli.create_container(image=image_name)
    cli.start(container=container.get('Id'))
    # Get all installed packages
    products = get_soft_from_docker_container_id(cli, container.get('Id'))
    # Stop container
    cli.stop(container=container.get('Id'))
    # Return packages
    return products


# Gets installed software from docker container id
def get_soft_from_docker_container_id(cli, container_id):
    # Extract Linux image distribution
    dict = cli.exec_create(container=container_id,cmd='cat /etc/os-release', stderr=False)
    response = get_os_name((cli.exec_start(exec_id=dict.get('Id'))).decode("utf-8"))
    # Get all installed packages
    if 'Red Hat' in response or 'CentOS' in response or 'Fedora' in response:  # Red Hat/CentOS/Fedora
        dict = cli.exec_create(container=container_id, cmd='rpm -aqi', stderr=False)
        packages_info = (cli.exec_start(exec_id=dict.get('Id'))).decode("utf-8")
        products = parse_rpm_output_list(packages_info)
    elif 'Debian' in response or 'Ubuntu' in response:   # Debian/Ubuntu
        dict = cli.exec_create(container=container_id, cmd='dpkg -l', stderr=False)
        packages_info = (cli.exec_start(exec_id=dict.get('Id'))).decode("utf-8")
        products = parse_dpkg_output_list(packages_info)
    elif 'Alpine' in response:    # Alpine
        dict = cli.exec_create(container=container_id, cmd='apk -v info', stderr=False)
        packages_info = (cli.exec_start(exec_id=dict.get('Id'))).decode("utf-8")
        products = parse_apk_output_list(packages_info)
    else:
        print('Error: Linux image distribution not supported yet.', file=sys.stderr)
        exit(1)
    # Return packages
    return products


# Gets the docker image name from a running container
def get_docker_image_name_from_container_id(cli, container_id):
    containers = cli.containers(filters={'id': container_id})
    return containers[0]['Image']


# Gets OS name from /etc/os-release file
def get_os_name(os_release):
    lines = os_release.split('\n')
    for line in lines:
        if line.startswith('NAME='):
            return line


# Parses the rpm output returned by docker container
def parse_rpm_output_list(packages_info):
    package_lines = packages_info.split('\n')
    counter = 0
    products = []
    for line in package_lines:
        if line.startswith("Name        :") or line.startswith("Version     :"):
            info = line.split(':')[1].rstrip().lstrip()
            if counter == 0:
                product = info
                counter += 1
            else:
                version = info
                counter = 0
                data = {}
                data['product'] = product
                data['version'] = version
                products.append(data)
    return products


# Parses the dpkg output returned by docker container
def parse_dpkg_output_list(packages_info):
    package_lines = packages_info.split('\n')
    products = []
    for line in package_lines:
        data = {}
        if line.startswith("ii"):
            splitted_line = re.split('\s+', line)
            # Get product name
            if ':' in splitted_line[1]:
                pos = splitted_line[1].index(':')
                product = splitted_line[1][0:pos]
            else:
                product = splitted_line[1]
            data['product'] = product
            # Get version
            version = splitted_line[2]
            if '-' in version:
                pos = version.index('-')
                version = splitted_line[2][0:pos]
            if ':' in version:
                pos = version.index(':')
                version = version[pos+1:]
            data['version'] = version
            products.append(data)
    return products


# Parses the apk info output returned by docker container
def parse_apk_output_list(packages_info):
    package_lines = packages_info.split('\n')
    products = []
    for line in package_lines:
        data = {}
        if (re.search("(.*)-([0-9].*)", line)):
            splitted_line = re.match("(.*)-([0-9].*)", line)
            # Get product name
            data['product'] = splitted_line.group(1)
            # Get version
            version = splitted_line.group(2)
            if '-' in version:
                pos = version.index('-')
                version = version[0:pos]
            data['version'] = version
            products.append(data)
    return products


# Evaluates all products installed in the docker image
def evaluate_products(image_name, products):
    data = {}
    data['image_name'] = image_name
    data['timestamp'] = datetime.datetime.now().timestamp()
    products_status = []
    vuln_products = 0
    for product in products:
        p = {}
        p['product'] = product['product']
        p['version'] = product['version']
        p['status'] = check_cves(product['product'], product['version'])
        if p['status'] == 'VULN':
            vuln_products += 1
        products_status.append(p)
    data['evaluated_packages_info'] = products_status
    data['total_products'] = len(products_status)
    data['vuln_products'] = vuln_products
    data['ok_products'] = data['total_products'] - vuln_products
    return data


# Checks if product with version has vulnerabilities
def check_cves(product, version):
    m = MongoDbDriver()
    if m.has_cves(product, version):
        return 'VULN'
    else:
        return 'OK'


# Main function
def main(parsed_args):
    m = MongoDbDriver()
    if not parsed_args.is_history_requested():
        cli = docker.Client(base_url='unix://var/run/docker.sock', version="auto")
        # Scans the docker image/container
        if parsed_args.get_docker_image_name():   # Scan the docker image
            products = get_soft_from_docker_image(cli, parsed_args.get_docker_image_name())
            image_name = parsed_args.get_docker_image_name()
        else:   # Scan the docker container
            products = get_soft_from_docker_container_id(cli, parsed_args.get_container_id())
            image_name = get_docker_image_name_from_container_id(cli, parsed_args.get_container_id())
        # Evaluate the installed software
        evaluated_docker_image = evaluate_products(image_name, products)
        # Update the scan history
        m.insert_docker_image_scan_result_to_history(evaluated_docker_image)
        # Prepares output
        evaluated_docker_image['timestamp'] = str(
            datetime.datetime.utcfromtimestamp(evaluated_docker_image['timestamp']))
        del evaluated_docker_image['_id']
        print(json.dumps(evaluated_docker_image))
    else:   # Gets the history
        print(json.dumps(m.get_docker_image_history(parsed_args.get_docker_image_name())))


if __name__ == "__main__":
    main(CheckDockerImageCLIParser())
