# -*- coding: utf-8 -*-

import json
from typing import List, Dict, Any

from .utils import get_instances_for_user, get_name, username, find_key


def get_live_ondemands(full: bool) -> List[Dict[str, Any]]:
    user_instances = get_instances_for_user(username())

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
            data["Type"] = instance["InstanceType"]
            data["Key File"] = str(find_key(instance["KeyName"]))

        rows.append(data)

    return rows
