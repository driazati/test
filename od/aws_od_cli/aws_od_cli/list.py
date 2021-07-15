import json

from .utils import *


def get_live_ondemands(full: bool):
    user_instances = get_instances_for_user(username())

    with open(INSTANCES_PATH, "r") as f:
        saved_instances = json.load(f)

    rows = []
    for instance in user_instances:
        state = instance["State"]["Name"]
        if state == "terminated":
            continue

        data = {
            "Name": get_name(instance),
            "Status": instance["State"]["Name"],
            "Launched": instance["LaunchTime"]
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S"),
        }

        if full:
            id = instance["InstanceId"]
            data["Instance Id"] = id
            data["DNS"] = instance["PublicDnsName"]

            if id in saved_instances:
                data["Key File"] = str(saved_instances[id]["key_path"])
            else:
                data["Key File"] = "<unknown>"

        rows.append(data)

    return rows
