#!/usr/bin/env python3

#author : Bryce Wade Pearson
"""A script to pull information from ECS for EC2 vs. Fargate comparison."""
import argparse
import datetime
import json
import os
import re

import boto3
import xlsxwriter

##########
# Globals
##########

price_history = {}

REGION_MAP = {
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ca-central-1": "Canada (Central)",
    "eu-central-1": "EU (Frankfurt)",
    "eu-north-1": "EU (Stockholm)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)",
    "sa-east-1": "South America (Sao Paulo)",
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)"
}
FARGATE_SIZES = {
    0.25: [0.5, 1, 2],
    0.5: range(1, 4),
    1: range(2, 9),
    2: range(4, 17),
    4: range(8, 31)
}


def datetimeconverter(item):
    """Return a string version of datetime."""
    # This is so we can do a json.dumps of something with datetime data
    if isinstance(item, datetime.datetime):
        return item.__str__()
    return item


def parse_args():
    """Parse the commandline arguements for later consumption."""

    parser = argparse.ArgumentParser(
        description=('Query ecs clusters and analyize what consumption '
                     'presently and how it would  '))

    parser.add_argument(
        '-f',
        '--filename',
        type=str,
        action="store",
        default='{}-ECS.xlsx'.format(os.getenv('AWS_PROFILE')),
        help='Specify a filename for the file to be created')

    parser.add_argument(
        '-r',
        '--region',
        type=str,
        action="store",
        default=os.getenv(
            'AWS_REGION',
            os.getenv('AWS_DEFAULT_REGION')),
        help='Specify an AWS region')

    parser.add_argument(
        '-c',
        '--cpu-fudge',
        type=int,
        default=0,
        action="store",
        help='Specify a "fudge" factor to round down CPU Utilizaiton to '
             'better fit within fargate configutations (0-100)')

    parser.add_argument(
        '-A',
        '--aws-discount',
        type=int,
        action="store",
        default=23,
        help='Percentage value of discount to calculate on General '
             'AWS costs (0-100)')

    parser.add_argument(
        '-F',
        '--fargate-discount',
        type=int,
        action="store",
        default=33,
        help='Percentage value of discount to calculate on '
             'Fargate specitic AWS costs (0-100)')

    args = vars(parser.parse_args())

    # Here we're modifying meatbag friendly values to values
    # that python can more readily use
    args = {
        'aws_discount': float(args.get('aws_discount') / 100),
        'cpu_fudge': 1 + float(args.get('cpu_fudge') / 100),
        'fargate_discount': float(args['fargate_discount'] / 100),
        'filename': args.get('filename')+".xlsx"
        if not re.search(r"\.xlsx$", args['filename'], re.IGNORECASE)
        else args.get('filename'),
        'region': args.get('region').lower()
    }

    return args


def get_cluster_list(client):
    """Return a list of cluster ARNs."""
    paginator = client.get_paginator('list_clusters')
    response_iterator = paginator.paginate()
    clusters = []
    for response in response_iterator:
        clusters.extend(response.get('clusterArns', []))
    return clusters


# Given a list of dictionaries return the one with the correct name
# (We could probably do this with a lambda call, but I think this is easier
# to read)
def find_dict(name, list_of_dicts):
    """Return the dictionary with the specified name."""
    for entry in list_of_dicts:
        if entry['name'] == name:
            return entry
    return None


# In a list of maps that looks like this return the value for a specified name
# [
#     {
#         "name": "sample_name1",
#         "value": "sample_value1"
#     },
#         "name": "sample_name2",
#         "value": "sample_value2"
#     }
# ]
def get_attribute(attribute, instance):
    """Return the value for a dictionary matching the specified name."""
    entry = find_dict(attribute, instance.get('attributes', []))
    if entry is not None:
        return entry.get('value')
    return None


# Gather the info about an EC2 instance that is part of an ECS cluster
def get_instance_info(instance):
    """Return size info for a specified ECS EC2 instance."""
    info = {}
    info['instance-type'] = get_attribute('ecs.instance-type', instance)
    remaining_resources = instance.get('remainingResources', {})
    remaining_cpu = find_dict('CPU', remaining_resources)
    info['remaining_cpu'] = remaining_cpu.get('integerValue')
    remaining_memory = find_dict('MEMORY', remaining_resources)
    info['remaining_memory'] = remaining_memory.get('integerValue')
    registered_resources = instance.get('registeredResources', {})
    total_cpu = find_dict('CPU', registered_resources)
    info['total_cpu'] = total_cpu.get('integerValue')
    total_memory = find_dict('MEMORY', registered_resources)
    info['total_memory'] = total_memory.get('integerValue')
    return info


# Add up the size info about the cluster
def add_to_running_total(info, total):
    """Add the new info to the running total for an ECS cluster."""
    total['instance-type'] = info['instance-type']
    total['instance-count'] = total.get('instance-count', 0) + 1
    total['remaining_cpu'] = total.get(
        'remaining_cpu', 0) + info['remaining_cpu']
    total['remaining_memory'] = total.get(
        'remaining_memory', 0) + info['remaining_memory']
    total['total_cpu'] = total.get('total_cpu', 0) + info['total_cpu']
    total['total_memory'] = total.get('total_memory', 0) + info['total_memory']
    return total


def get_ec2_price(instance_type, aws_discount, region):
    """Pull the current price of an EC2 instance type for the given region."""
    price = price_history.get(instance_type)
    if price is not None:
        return price
    client = boto3.client('pricing', region_name='us-east-1')
    # We need to filter things down enough that we only get the price we
    # want
    response = client.get_products(
        ServiceCode='AmazonEC2',
        Filters=[
            {
                'Type': 'TERM_MATCH',
                'Field': 'instanceType',
                'Value': instance_type
            },
            {
                'Type': 'TERM_MATCH',
                'Field': 'operatingSystem',
                'Value': 'Linux'
            },
            {
                'Type': 'TERM_MATCH',
                'Field': 'location',
                'Value': REGION_MAP[region]
            },
            {
                'Type': 'TERM_MATCH',
                'Field': 'preInstalledSw',
                'Value': 'NA'
            }
        ]
    )
    pricelist = response.get('PriceList', [])
    # parse through the returned price list and return the price we are looking
    # for
    for entry in pricelist:
        price_entry = json.loads(entry)
        usage_type = price_entry.get('product', {}).get(
            'attributes', {}).get('usagetype')
        if usage_type.endswith('UnusedBox:{}'.format(instance_type)):
            terms = price_entry.get('terms', {})
            on_demand = terms.get('OnDemand', {})
            offer_term = next(iter(on_demand.values()))
            rate_code = next(
                iter(offer_term.get('priceDimensions', {}).values()))
            list_price = rate_code.get('pricePerUnit', {}).get('USD')
            if list_price:
                price = float(list_price) * (1 - aws_discount)
                price_history[instance_type] = price
                return price
    return None


def get_fargate_cpu_price(fargate_discount, region):
    """Pull the current Fargate CPU cost for the given region."""
    price = price_history.get('fargate_cpu')
    if price is not None:
        return price
    client = boto3.client('pricing', region_name='us-east-1')
    # Filter the prices down
    response = client.get_products(
        ServiceCode='AmazonECS',
        Filters=[
            {
                'Type': 'TERM_MATCH',
                'Field': 'cputype',
                'Value': 'perCPU'
            },
            {
                'Type': 'TERM_MATCH',
                'Field': 'tenancy',
                'Value': 'Shared'
            },
            {
                'Type': 'TERM_MATCH',
                'Field': 'location',
                'Value': REGION_MAP[region]
            }
        ]
    )
    pricelist = response.get('PriceList', [])
    if pricelist:
        price_entry = json.loads(pricelist[0])
        terms = price_entry.get('terms', {})
        on_demand = terms.get('OnDemand', {})
        offer_term = next(iter(on_demand.values()))
        rate_code = next(iter(offer_term.get('priceDimensions', {}).values()))
        list_price = rate_code.get('pricePerUnit', {}).get('USD')
        if list_price:
            price = float(list_price) * (1 - fargate_discount)
            price_history['fargate_cpu'] = price
            return price
    return None


def get_fargate_memory_price(fargate_discount, region):
    """Pull the current Fargate memory cost for the given region."""
    price = price_history.get('fargate_memory')
    if price is not None:
        return price
    client = boto3.client('pricing', region_name='us-east-1')
    response = client.get_products(
        ServiceCode='AmazonECS',
        Filters=[
            {
                'Type': 'TERM_MATCH',
                'Field': 'memorytype',
                'Value': 'perGB'
            },
            {
                'Type': 'TERM_MATCH',
                'Field': 'tenancy',
                'Value': 'Shared'
            },
            {
                'Type': 'TERM_MATCH',
                'Field': 'location',
                'Value': REGION_MAP[region]
            }
        ]
    )
    pricelist = response.get('PriceList', [])
    if pricelist:
        price_entry = json.loads(pricelist[0])
        terms = price_entry.get('terms', {})
        on_demand = terms.get('OnDemand', {})
        offer_term = next(iter(on_demand.values()))
        rate_code = next(iter(offer_term.get('priceDimensions', {}).values()))
        list_price = rate_code.get('pricePerUnit', {}).get('USD')
        if list_price:
            price = float(list_price) * (1 - fargate_discount)
            price_history['fargate_memory'] = price
            return price
    return None


# Get a list of EC2 instances for a cluster and return the aggregated sizing
# info for that cluster
def get_container_stats(client, cluster):
    """Return infomation about ECS EC2 instances for a cluster."""
    paginator = client.get_paginator('list_container_instances')
    response_iterator = paginator.paginate(cluster=cluster)
    running_total = {}
    for response in response_iterator:
        instances = response.get('containerInstanceArns', [])
        if instances:
            instance_response = client.describe_container_instances(
                cluster=cluster,
                containerInstances=instances
            )
            for instance in instance_response.get('containerInstances', []):
                info = get_instance_info(instance)
                running_total = add_to_running_total(info, running_total)
    return running_total


# Given an array of data add that to a specific row of the specified Excel
# worksheet
def add_row_to_sheet(worksheet, row, data):
    """Add an entire row to the Excel worksheet."""
    column = 0
    for value in data:
        worksheet.write(row, column, value)
        column = column + 1


def create_ec2_sheet(workbook, currency_fmt, percent_fmt,
                     cluster_totals, args):
    """Create an Excel worksheet for the EC2 usage in a region."""
    aws_discount = args['aws_discount']
    region = args['region']
    worksheet = workbook.add_worksheet('EC2 Usage')
    add_row_to_sheet(worksheet, 0, [
        'Cluster',
        'Instance Type',
        'Instance Count',
        'Instance $/Hr',
        'Total $/Hr',
        'Used CPU',
        'Unused CPU',
        'Total CPU',
        'Used Memory',
        'Unused Memory',
        'Total Memory',
        'Wasted CPU',
        'Wasted Memory'
    ])
    row = 0
    for cluster, totals in sorted(cluster_totals.items()):
        if totals:
            row = row + 1
            # Populate unique data with numbers, but allow Excel to calculate
            # as much data as possible so that humans can easily play see
            # what-if scenarios
            add_row_to_sheet(worksheet, row, [
                cluster.split("/")[1],
                totals['instance-type'],
                totals['instance-count'],
                get_ec2_price(totals['instance-type'], aws_discount, region),
                '=C{0}*D{0}'.format(row + 1),
                '=H{0}-G{0}'.format(row + 1),
                totals['remaining_cpu'],
                totals['total_cpu'],
                '=K{0}-J{0}'.format(row + 1),
                totals['remaining_memory'],
                totals['total_memory'],
                '=G{0}/H{0}'.format(row + 1),
                '=J{0}/K{0}'.format(row + 1)
            ])
    # Set columns D and E as currency
    worksheet.set_column('D:E', None, currency_fmt)
    # And columns L and M as percentages
    worksheet.set_column('L:M', None, percent_fmt)
    return row + 1


def get_services(client, cluster):
    """Return a list of services for a given ECS cluster."""
    paginator = client.get_paginator('list_services')
    response_iterator = paginator.paginate(
        cluster=cluster,
        schedulingStrategy='REPLICA'
    )
    services = []
    # Cycle through all the pages and add the serviceArns to our list of
    # services
    for response in response_iterator:
        services.extend(response.get('serviceArns', []))
    return services


def adjust_task_size(task_cpu, task_mem, cpu_fudge_factor):
    """Return a valid Fargate configuration for specified cpu and memory."""
    for cpu in sorted(FARGATE_SIZES.keys()):
        if task_cpu < cpu * cpu_fudge_factor:
            for mem in FARGATE_SIZES[cpu]:
                if task_mem < mem:
                    return (cpu, mem)
    return (None, None)


def get_task_size(client, task_def, cpu_fudge_factor):
    """Return the size information for a given task definition."""
    if task_def is None:
        return {}
    response = client.describe_task_definition(taskDefinition=task_def)
    task_definition = response.get('taskDefinition', {})
    containers = task_definition.get('containerDefinitions', [])
    cpu = 0
    memory = 0
    # Cycle through all the containers in the task definiton and add up
    # all the cpu & memory reservations
    for container in containers:
        cpu = cpu + container['cpu']
        memory = memory + container.get(
            'memory', container.get('memoryReservation'))
    (acpu, amem) = adjust_task_size(cpu / 1024, memory / 1024,
                                    cpu_fudge_factor)
    task_size = {
        'vcpu': acpu,
        'GB': amem,
        'cpu': cpu,
        'mem': memory
    }
    return task_size


def get_service_info(client, cluster, service, cpu_fudge_factor):
    """Return the size information of a given service."""
    response = client.describe_services(
        cluster=cluster,
        services=[service]
    )
    services = response.get('services')
    # Cycle through all the services and get info about them
    if services:
        task_def = services[0].get('taskDefinition')
        running = int(services[0].get('runningCount', 0))
        if running == 0:
            return {}
        service_info = get_task_size(client, task_def, cpu_fudge_factor)
        service_info['running'] = running
        return service_info
    return {}


def get_service_stats(ecs, cluster, cpu_fudge_factor):
    """Gather the service size stats for a given cluster."""
    service_list = get_services(ecs, cluster)
    services = {}
    for service in service_list:
        service_info = get_service_info(ecs, cluster, service,
                                        cpu_fudge_factor)
        services[service] = service_info
    return services


def create_fargate_sheet(workbook, currency_fmt, cluster_info,
                         fargate_discount, region):
    """Create an Excel worksheet for the proposed Fargate usage in a region."""
    worksheet = workbook.add_worksheet('Fargate Usage')
    add_row_to_sheet(worksheet, 0, [
        'Cluster',
        'Service',
        'Tasks running',
        'CPU shares',
        'MB Memory',
        'vCPU',
        'GB Mem',
        'vCPU $/Hr',
        'Total vCPU $/Hr',
        'GB $/Hr',
        'Total GB $/Hr',
        'Total Cost $/Hr'
    ])
    row = 0
    # cycle through the clusters and services to create rows for them in the
    # Fargate sheet.  Set up formulas so that Excel can do calculations for us
    # allowing humans to more easily evaluate what-if scenarios.
    for cluster, services in sorted(cluster_info.items()):
        for service, value in sorted(services.items()):
            if value.get('running', 0) > 0:
                row = row + 1
                add_row_to_sheet(worksheet, row, [
                    cluster.split("/")[1],
                    service.split("/")[-1],
                    value['running'],
                    value['cpu'],
                    value['mem'],
                    value['vcpu'],
                    value['GB'],
                    get_fargate_cpu_price(fargate_discount, region),
                    '=C{0}*F{0}*H{0}'.format(row + 1),
                    get_fargate_memory_price(fargate_discount, region),
                    '=C{0}*G{0}*J{0}'.format(row + 1),
                    '=I{}+K{}'.format(row + 1, row + 1),
                ])
    worksheet.set_column('H:L', None, currency_fmt)
    return row + 1


def create_comparison_sheet(workbook, currency_fmt,
                            cluster_totals, ec2_rows, fargate_rows):
    """Create an Excel worksheet comparing the EC2 & Fargate usage."""
    sheetname = 'Comparison'
    worksheet = workbook.add_worksheet(sheetname)
    add_row_to_sheet(worksheet, 0, [
        'Cluster',
        'Fargate Cost',
        'EC2 Cost',
        'Winner'
    ])
    row = 0
    # Cycle through all the clusters and create a row for them in the
    # comparison sheet that compares the cost in EC2 vs. the expected cost
    # in Fargate.  Again, use formulas so humans can more easily evaluate
    # what-if scenarios
    for cluster, totals in sorted(cluster_totals.items()):
        if totals:
            row = row + 1
            add_row_to_sheet(worksheet, row, [
                cluster.split("/")[1],
                ("=SUMIF('Fargate Usage'!A2:A{0},{1}!A{2},"
                 "'Fargate Usage'!L2:L{0})").format(
                     fargate_rows, sheetname, row + 1),
                "=VLOOKUP(A{0},'EC2 Usage'!A2:E{1},5)".format(
                    row + 1, ec2_rows),
                '=IF(B{0}<C{0},"Fargate", "EC2")'.format(row + 1)
            ])
    worksheet.set_column('B:C', None, currency_fmt)


def create_wasted_cpu_charts(workbook, ec2_rows):
    """Add a chart for CPU usage to the Excel workbook."""
    chartsheet = workbook.add_chartsheet('CPU Usage')
    chart = workbook.add_chart({'type': 'column', 'subtype': 'stacked'})
    chart.add_series({'values': "='EC2 Usage'!$F$2:$F${}".format(ec2_rows),
                      'categories': "='EC2 Usage'!$A$2:$A${}".format(ec2_rows),
                      'name': "='EC2 Usage'!$F$1"})
    chart.add_series({'values': "='EC2 Usage'!$G$2:$G${}".format(ec2_rows),
                      'categories': "='EC2 Usage'!$A$2:$A${}".format(ec2_rows),
                      'name': "='EC2 Usage'!$G$1"})
    chart.set_x_axis({'name': 'Cluster'})
    chart.set_y_axis({'name': 'CPU Units'})
    chart.set_title({'name': 'CPU Usage'})
    chartsheet.set_chart(chart)


def create_wasted_mem_charts(workbook, ec2_rows):
    """Add a chart for memory usage to the Excel workbook."""
    chartsheet = workbook.add_chartsheet('Memory Usage')
    chart = workbook.add_chart({'type': 'column', 'subtype': 'stacked'})
    chart.add_series({'values': "='EC2 Usage'!$I$2:$I${}".format(ec2_rows),
                      'categories': "='EC2 Usage'!$A$2:$A${}".format(ec2_rows),
                      'name': "='EC2 Usage'!$I$1"})
    chart.add_series({'values': "='EC2 Usage'!$J$2:$J${}".format(ec2_rows),
                      'categories': "='EC2 Usage'!$A$2:$A${}".format(ec2_rows),
                      'name': "='EC2 Usage'!$J$1"})
    chart.set_x_axis({'name': 'Cluster'})
    chart.set_y_axis({'name': 'MB'})
    chart.set_title({'name': 'Memory Usage'})
    chartsheet.set_chart(chart)


def main():
    """The main fuction for ECS for EC2 vs. Fargate comparison."""
    args = parse_args()

    for keyname in args:
        print("{} set to {}".format(keyname, str(args[keyname])))

    print("\n")

    workbook = xlsxwriter.Workbook(args['filename'])
    currency_fmt = workbook.add_format({'num_format': '$#,##0.00'})
    percent_fmt = workbook.add_format()
    percent_fmt.set_num_format(9)
    # Get our ECS clieant and a list of all the ECS clusters
    ecs = boto3.client('ecs')
    clusters = get_cluster_list(ecs)
    cluster_ec2_totals = {}
    cluster_ecs_totals = {}
    # Do all the magic to tather the necessary information
    for cluster in clusters:
        print("Gathering info for ECS Cluster {}".format(
            cluster.split("/")[1]))
        cluster_ec2_totals[cluster] = get_container_stats(ecs, cluster)
        cluster_ecs_totals[cluster] = get_service_stats(ecs, cluster,
                                                        args['cpu_fudge'])

    # Now create and populate the worksheets in that workbook
    ec2_rows = create_ec2_sheet(
        workbook, currency_fmt, percent_fmt, cluster_ec2_totals, args)
    fargate_rows = create_fargate_sheet(
        workbook, currency_fmt, cluster_ecs_totals, args['fargate_discount'],
        args['region'])
    create_comparison_sheet(workbook, currency_fmt,
                            cluster_ec2_totals, ec2_rows, fargate_rows)
    create_wasted_cpu_charts(workbook, ec2_rows)
    create_wasted_mem_charts(workbook, ec2_rows)
    workbook.close()


if __name__ == '__main__':
    main()
