#!/usr/bin/env python3
"""Check that every k8s/service-*.yaml Deployment has a NetworkPolicy in k8s/network-policy.yaml.

Exits 1 if any service is missing coverage, so CI fails before kubectl apply.
"""
import sys
import glob
import yaml


def load_docs(path):
    with open(path, encoding="utf-8") as f:
        return [d for d in yaml.safe_load_all(f) if d]


# 1. Collect app labels from all service Deployments
apps = set()
for path in sorted(glob.glob("k8s/service-*.yaml")):
    for doc in load_docs(path):
        if doc.get("kind") != "Deployment":
            continue
        labels = (
            doc.get("spec", {})
               .get("template", {})
               .get("metadata", {})
               .get("labels", {})
        )
        if "app" in labels:
            apps.add(labels["app"])

# 2. Collect apps that have a NetworkPolicy with podSelector.matchLabels.app
covered = set()
for doc in load_docs("k8s/network-policy.yaml"):
    if doc.get("kind") != "NetworkPolicy":
        continue
    match_labels = (
        doc.get("spec", {})
           .get("podSelector", {})
           .get("matchLabels", {})
    )
    if "app" in match_labels:
        covered.add(match_labels["app"])

# 3. Report
missing = apps - covered
if missing:
    print(
        f"ERROR: NetworkPolicy missing for: {', '.join(sorted(missing))}\n"
        "Add podSelector.matchLabels.app entries to k8s/network-policy.yaml.",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"OK: NetworkPolicy coverage verified for {sorted(apps)}")
