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

import logging
import os
import json
import argparse
import subprocess
import re
import datetime
from urllib import request
from urllib import error
from typing import Dict, Tuple, Any, List


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


def prs_query(user: str, repo: str, cursor: str = None):
    after = ""
    if cursor is not None:
        after = f', before:"{cursor}"'
    return f"""
        {{
    repository(name: "{repo}", owner: "{user}") {{
        pullRequests(states: [OPEN], last: 10{after}) {{
        edges {{
            cursor
        }}
        nodes {{
            number
            url
            body
            isDraft
            author {{
                login
            }}
            reviews(last:100) {{
                nodes {{
                    author {{ login }}
                    comments(last:100) {{
                        nodes {{
                            updatedAt
                            bodyText
                        }}
                    }}
                }}
            }}
            publishedAt
            comments(last:100) {{
                nodes {{
                    authorAssociation
                    bodyText
                    updatedAt
                    author {{
                        login
                    }}
                }}
            }}
        }}
        }}
    }}
    }}
    """


WAIT_TIME = datetime.timedelta(minutes=1)
CUTOFF_PR_NUMBER = 0
# CUTOFF_PR_NUMBER = 9000


def find_reviewers(body: str) -> List[str]:
    # print(f"Parsing body:\n{body}")
    matches = re.findall(r"(cc( @[-A-Za-z0-9]+)+)", body, flags=re.MULTILINE)
    matches = [full for full, last in matches]

    # print("Found matches:", matches)
    reviewers = []
    for match in matches:
        if match.startswith("cc "):
            match = match.replace("cc ", "")
        users = [x.strip() for x in match.split("@")]
        reviewers += users

    reviewers = set(x for x in reviewers if x != "")
    return list(reviewers)


def check_pr(pr):
    published_at = datetime.datetime.strptime(pr["publishedAt"], "%Y-%m-%dT%H:%M:%SZ")
    last_action = published_at

    # GitHub counts comments left as part of a review separately than standalone
    # comments
    reviews = pr["reviews"]["nodes"]
    review_comments = []
    for review in reviews:
        review_comments += review["comments"]["nodes"]

    # Collate all comments
    comments = pr["comments"]["nodes"] + review_comments

    # Find the last date of any comment
    for comment in comments:
        commented_at = datetime.datetime.strptime(
            comment["updatedAt"], "%Y-%m-%dT%H:%M:%SZ"
        )
        if commented_at > last_action:
            last_action = commented_at

    time_since_last_action = datetime.datetime.utcnow() - last_action

    # Pull out reviewers from any cc @... text in a comment
    cc_reviewers = [find_reviewers(c["bodyText"]) for c in comments]
    cc_reviewers = [r for revs in cc_reviewers for r in revs]

    # Anyone that has left a review as a reviewer (this may include the PR
    # author since their responses count as reviews)
    review_reviewers = list(set(r["author"]["login"] for r in reviews))

    reviewers = cc_reviewers + review_reviewers

    if time_since_last_action > WAIT_TIME:
        print(
            "    Pinging reviewers",
            reviewers,
            "on",
            pr["url"],
            "since it has been",
            time_since_last_action,
            "since anything happened on that PR",
        )
        return reviewers

    return None


def ping_reviewers(pr, reviewers):
    reviewers = [f"@{r}" for r in reviewers]
    text = (
        "It has been a while since this PR was updated, "
        + " ".join(reviewers)
        + " please leave a review or address the outstanding comments"
    )
    r = github.post(f"issues/{pr['number']}/comments", {"body": text})
    print(r)


if __name__ == "__main__":
    help = "Comment on languishing issues and PRs"
    parser = argparse.ArgumentParser(description=help)
    parser.add_argument("--remote", default="origin", help="ssh remote to parse")
    parser.add_argument("--dry-run", action="store_true", help="don't update GitHub")
    args = parser.parse_args()

    remote = git(["config", "--get", f"remote.{args.remote}.url"])
    user, repo = parse_remote(remote)

    print(
        "Running with:\n"
        f"  time cutoff: {WAIT_TIME}\n"
        f"  number cutoff: {CUTOFF_PR_NUMBER}\n"
        f"  dry run: {args.dry_run}\n"
        f"  user/repo: {user}/{repo}\n",
        end="",
    )

    github = GitHubRepo(token=os.environ["GITHUB_TOKEN"], user=user, repo=repo)

    q = prs_query(user, repo)
    r = github.graphql(q)

    # Loop until all PRs have been checked
    while True:
        prs = r["data"]["repository"]["pullRequests"]["nodes"]

        # Don't look at draft PRs at all
        prs = [pr for pr in prs if not pr["isDraft"]]

        # Don't look at super old PRs
        prs = [pr for pr in prs if pr["number"] > CUTOFF_PR_NUMBER]

        # Ping reviewers on each PR in the response if necessary
        for pr in prs:
            print("Checking", pr["url"])
            reviewers = check_pr(pr)
            if reviewers is not None and not args.dry_run:
                ping_reviewers(pr, reviewers)

        edges = r["data"]["repository"]["pullRequests"]["edges"]
        if len(edges) == 0:
            # No more results to check
            break

        cursor = edges[0]["cursor"]
        r = github.graphql(prs_query(user, repo, cursor))
