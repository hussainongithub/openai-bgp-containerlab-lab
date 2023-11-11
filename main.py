from openai import OpenAI
from ipaddress import ip_network
import yaml
import subprocess
import ast
import os

def generate_31_subnets(parent_prefix):
    """
    Generate /31 subnets from a given parent IPv4 prefix.

    :param parent_prefix: A string representation of the parent IPv4 prefix.
    :return: A list of subnetted /31 addresses.
    """
    # Create an IPv4 network object from the parent prefix
    network = ip_network(parent_prefix)
    # Check if the new prefix length /31 is larger than the parent prefix length
    if 31 <= network.prefixlen:
        raise ValueError(f"New prefix length /31 should be larger than parent prefix length {network.prefixlen}.")

    # Generate and return the /31 subnets
    return [subnet for subnet in network.subnets(new_prefix=31)]


def write_configuration_to_file(filename, lines):
    with open(filename, 'w') as f:
        for line in lines:
            f.write(line)


def generate_frrouter_addressing_info(subnets, parsed_data):
    rtr_configs = {}
    if 'links' in parsed_data:
        links = parsed_data['links']
        for link in links:
            a_end = link['endpoints'][0]
            b_end = link['endpoints'][1]
            a_router, a_link = a_end.split(':')
            b_router, b_link = b_end.split(':')

            s = subnets.pop()
            a_ip, b_ip = s.hosts()
            prefix = s.prefixlen

            if a_router not in rtr_configs:
                rtr_configs[a_router] = []

            rtr_configs[a_router].append(f"interface {a_link}" + "\n")
            rtr_configs[a_router].append(f" ip address {a_ip}/{prefix}" + "\n")

            if b_router not in rtr_configs:
                rtr_configs[b_router] = []

            rtr_configs[b_router].append(f"interface {b_link}" + "\n")
            rtr_configs[b_router].append(f" ip address {b_ip}/{prefix}" + "\n")
        return rtr_configs
    else:
        raise ValueError("No 'links' section found in the YAML data.")


def generate_topology(openai_client):
    prompt = """
    Create a YAML-formatted LLD for an ISP network with the following specifications:
    - Routers: {router_count}
    - Topology level of Redundancy (this can be defined as low, medium, high etc. Depending on the level, the topology will have additional elements of redundancy added in a manner which realistic and follows common ISP topologies): {redundancy_type}
    - Routing protocols: BGP (IPv4 address family) and OSPFv4
    - Random topology element (this is a random link added to make the topology more interesting, do not add any other random network devices): {random_element}

    Replace the placeholders with actual randomized values!

    The topology must not be full mesh and be structured in a way which mimics a typical ISP network (with core, aggregation and access sites)


    Your output will only be in YAML format with the following information:
    1. YAML formatted topology showing interconnections in the following format (using the ethx naming convention for interfaces and routerx for routers):

    links:
      - endpoints: ["router1:eth1", "router2:eth1"]
      - endpoints: ["router1:eth2", "router3:eth1"]

    2. Brief description of each router's role in the network in the following format:

    routers:
      - id: "router1"
        type: "Core"
        description: "Core router .."

    Please provide the response strictly in YAML format, without any accompanying explanation or descriptive text. Please do not include any comments (marked with a hashtag) in the YAML content.
    Please do not add the YAML code block (```yaml) surrounding your response.
    """
    completion = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system",
             "content": prompt}
        ]
    )
    return completion.choices[0].message.content


def generate_bgp_troubleshooting_scenario(openai_client, ip_data, stage_1_data):
    prompt = f"""
    Generate a detailed BGP troubleshooting scenario for a given network topology in JSON format. The network consists 
    of the following routers and links (defined in Python dictionary):
    {stage_1_data}
    
    The current router interface configurations (format in frrouter) are defined below (Python lists):
    {ip_data}
    
    Include the following details in the scenario
    A brief description of the network topology, specifying the role of the problematic router within the network.
    The intended BGP configuration and the expected behavior of the BGP session under normal conditions.
    BGP configurations for all routers (which can include the simulated issue)
    A list of symptoms and error messages that NOC engineers might observe due to this issue.
    Hints for troubleshooting steps that could be taken to diagnose the problem.
    
    Format the response in JSON, without any accompanying explanation or descriptive text, suitable for parsing in a script.
    The JSON response should be in the following format (example below):
    """
    json_string = '''{
      "description": "The network topology consists of ..",
      "expected_behavior": {
        "config": "Under normal circumstances ..",
        "session": "The BGP session ..."
      },
      "bgp_config": {
        "router1": "router bgp 65000\\n",
      },
      "symptoms": ["Intermittent network outages"],
      "troubleshooting_steps": ["Check BGP logs for error messages"]
    }'''
    completion = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system",
             "content": prompt+json_string}
        ]
    )
    return completion.choices[0].message.content


def generate_clab_yml_config(data):
    """ Generates clab yml file for a project """

    # Function to convert nodes data to YAML formatted string
    def nodes_to_yaml_string(nodes):
        yaml_str = "  nodes:\n"
        for node, details in nodes.items():
            yaml_str += f"    {node}:\n"
            for key, value in details.items():
                if isinstance(value, list):
                    yaml_str += f"      {key}:\n"
                    for item in value:
                        yaml_str += f"        - {item}\n"
                else:
                    yaml_str += f"      {key}: {value}\n"
        return yaml_str

    # Function to convert links data to YAML formatted string
    def links_to_yaml_string(links):
        yaml_str = "  links:\n"
        for link in links:
            endpoints = ', '.join(f'"{endpoint}"' for endpoint in link["endpoints"])
            yaml_str += f"    - endpoints: [{endpoints}]\n"
        return yaml_str

    # Combine everything into one YAML string
    yaml_content = f"name: {data['name']}\n\n"
    yaml_content += "topology:\n"

    if 'nodes' in data['topology']:
        yaml_content += nodes_to_yaml_string(data['topology']['nodes'])

    if 'links' in data['topology']:
        yaml_content += links_to_yaml_string(data['topology']['links'])

    return yaml_content


def start_gpt4_chat(openai_client, stage_1_data, stage_2_data, ip_data):
    prompt = f"""
    You are a network instructuctor. The following Python list and dictionaries contain the topology information as well
    as frrouter configurations for a lab scenario. 
    {stage_1_data}
    {stage_2_data}
    {ip_data}
    From now, you will help the student find the solution to the BGP issue. Don't reveal the answer immediately to them 
    but guide them towards the solution.
    """
    messages = [
        {"role": "system", "content": prompt},
    ]

    while True:
        # Prompt user for input
        message = input("User: ")

        # Exit program if user inputs "quit"
        if message.lower() == "quit":
            break

        # Add each new message to the list
        messages.append({"role": "user", "content": message})

        # Request gpt-4 for chat completion
        completion = openai_client.chat.completions.create(
            model="gpt-4",
            messages=messages
        )

        # Print the response and add it to the messages list
        chat_message = completion.choices[0].message.content
        print(f"LLM: {chat_message}")
        messages.append({"role": "assistant", "content": chat_message})


def main():
    client = OpenAI(
        api_key=os.environ.get('OPENAI_KEY')
    )

    # generate /31 subnets for lab topology
    parent_prefix = '10.254.0.0/24'  # The parent prefix you want to subnet
    subnets = generate_31_subnets(parent_prefix)

    # Stage 1: generate topology
    stage_1_prompt_data = generate_topology(openai_client=client)

    print("DEBUG Stage 1 prompt data", stage_1_prompt_data)

    # load output from Stage 1 into YAML
    parsed_data = yaml.safe_load(stage_1_prompt_data)

    # generate router int/IP configs
    rtr_configs = generate_frrouter_addressing_info(subnets, parsed_data)

    # make a copy of the IP config for Stage 4
    ip_data = rtr_configs.copy()

    print("DEBUG IP DATA", ip_data)

    # Stage 2: generate BGP troubleshooting scenario
    stage_2_prompt_data = generate_bgp_troubleshooting_scenario(openai_client=client,
                                                                ip_data=rtr_configs,
                                                                stage_1_data=parsed_data)

    # Stage 3: Create and deploy containerlabs topology locally

    # Add base configs to existing configuration
    for router in rtr_configs:
        rtr_configs[router].insert(0, 'frr defaults traditional \n')
        rtr_configs[router].insert(0, f'hostname {router} \n')
        rtr_configs[router].insert(0, 'no ipv6 forwarding \n')
        rtr_configs[router].append('router ospf \n')
        rtr_configs[router].append(' network 10.254.0.0/16 area 0.0.0.0 \n')
        rtr_configs[router].append('line vty \n')

    # Append BGP configurations to existing configuration
    stage_2_prompt_data = ast.literal_eval(stage_2_prompt_data)  # convert stage 2 prompt data from literal str to dict

    print("DEBUG STAGE 2", stage_2_prompt_data)

    for router, config in stage_2_prompt_data['bgp_config'].items():
        rtr_configs[router].append(config)

    # write configs to local directory
    for router, config in rtr_configs.items():
        filename = f'{router}_frr.conf'
        write_configuration_to_file(filename, config)

    # define clab yaml file
    topology_data = {
        'name': 'lab_example',
        'topology': {
            'nodes': {},
            'links': parsed_data['links']
        }
    }

    # populate nodes in topology_data
    for router, config in rtr_configs.items():
        topology_data['topology']['nodes'][router] = {}
        topology_data['topology']['nodes'][router] = {
            'kind': 'linux',
            'image': 'frrouting/frr:v7.5.1',
            'binds': ['./daemons:/etc/frr/daemons', f'{router}_frr.conf:/etc/frr/frr.conf']
        }

    # generate clab yml file and write to file
    file = generate_clab_yml_config(topology_data)
    write_configuration_to_file(f'{topology_data["name"]}.clab.yml', file)

    # deploy containerlab topology
    command = "containerlab deploy"
    process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
    output, error = process.communicate()
    if error:
        raise ValueError(f"Error occurred while deploying containerlab topology: {error}")

    # Stage 4: Open prompt to help the user troubleshoot scenario
    start_gpt4_chat(openai_client=client, stage_1_data=parsed_data, stage_2_data=stage_2_prompt_data, ip_data=ip_data)


if __name__ == "__main__":
    main()
