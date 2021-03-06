#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import os
import json
import argparse
import subprocess
import re
from urllib import request
from urllib import error
from typing import Dict, Tuple, Any


def commit_query(repo: str, user: str, sha: str) -> str:
    return f"""
    {{
    repository(name: "{repo}", owner: "{user}") {{
        object(oid: "{sha}") {{
        ... on Commit {{
            associatedPullRequests(last:1) {{
            nodes {{
                number
                reviewDecision
                commits(last:1) {{
                nodes {{
                    commit {{
                    statusCheckRollup {{
                        contexts(last:100) {{
                        nodes {{
                            ... on CheckRun {{
                            conclusion
                            status
                            name
                            checkSuite {{
                                workflowRun {{
                                workflow {{
                                    name
                                }}
                                }}
                            }}
                            }}
                            ... on StatusContext {{
                            context
                            state
                            }}
                        }}
                        }}
                    }}
                    }}
                }}
                }}
            }}
            }}
        }}
        }}
    }}
    }}"""


class GitHubRepo:
    def __init__(self, user, repo, token):
        self.token = token
        self.user = user
        self.repo = repo
        self.base = f"https://api.github.com/repos/{user}/{repo}/"

    def headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
        }

    def graphql(self, query: str) -> Dict[str, Any]:
        return self._post("https://api.github.com/graphql", {"query": query})

    def _post(self, full_url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        print("Requesting", full_url)
        req = request.Request(full_url, headers=self.headers(), method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        data = json.dumps(body)
        data = data.encode("utf-8")
        req.add_header("Content-Length", len(data))

        with request.urlopen(req, data) as response:
            response = json.loads(response.read())
        return response

    def post(self, url: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._post(self.base + url, data)

    def get(self, url: str) -> Dict[str, Any]:
        url = self.base + url
        print("Requesting", url)
        req = request.Request(url, headers=self.headers())
        with request.urlopen(req) as response:
            response = json.loads(response.read())
        return response

    def delete(self, url: str) -> Dict[str, Any]:
        url = self.base + url
        print("Requesting", url)
        req = request.Request(url, headers=self.headers(), method="DELETE")
        with request.urlopen(req) as response:
            response = json.loads(response.read())
        return response


def parse_remote(remote: str) -> Tuple[str, str]:
    """
    Get a GitHub (user, repo) pair out of a git remote
    """
    if remote.startswith("https://"):
        # Parse HTTP remote
        parts = remote.split("/")
        if len(parts) < 2:
            raise RuntimeError(f"Unable to parse remote '{remote}'")
        return parts[-2], parts[-1].replace(".git", "")
    else:
        # Parse SSH remote
        m = re.search(r":(.*)/(.*)\.git", remote)
        if m is None or len(m.groups()) != 2:
            raise RuntimeError(f"Unable to parse remote '{remote}'")
        return m.groups()


def git(command):
    proc = subprocess.run(["git"] + command, stdout=subprocess.PIPE, check=True)
    return proc.stdout.decode().strip()


def is_pr_ready(data: Any) -> bool:
    approved = data["reviewDecision"] == "APPROVED"
    print("Is approved?", approved)

    statuses = data["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"][
        "nodes"
    ]
    unified_statuses = []
    for status in statuses:
        if "context" in status:
            unified_statuses.append((status["context"], status["state"] == "SUCCESS"))
            # non-GHA
        else:
            # GitHub Actions
            workflow = status["checkSuite"]["workflowRun"]["workflow"]["name"]
            name = f"{workflow} / {status['name']}"
            unified_statuses.append((name, status["conclusion"] == "SUCCESS"))

    print("Got statuses:", json.dumps(unified_statuses, indent=2))
    passed_ci = all(status for name, status in unified_statuses)
    return approved and passed_ci


if __name__ == "__main__":
    help = "Exits with 0 if CI should be skipped, 1 otherwise"
    parser = argparse.ArgumentParser(description=help)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--remote", default="origin", help="ssh remote to parse")
    args = parser.parse_args()

    remote = git(["config", "--get", f"remote.{args.remote}.url"])
    user, repo = parse_remote(remote)
    github = GitHubRepo(token=os.environ["GITHUB_TOKEN"], user=user, repo=repo)

    data = github.graphql(
        commit_query(repo, user, args.sha)
    )
    pr = data["data"]["repository"]["object"]["associatedPullRequests"]["nodes"][0]

    if is_pr_ready(pr):
        print("PR passed CI and is approved, labelling...")
        github.post(f"issues/{pr['number']}/labels", {"labels": ["ready-for-merge"]})
    else:
        print("PR is not ready for merge")
        try:
            github.delete(f"issues/{pr['number']}/labels/ready-for-merge")
        except error.HTTPError as e:
            print(e)
            print("Failed to remove label (it may not have been there at all)")
