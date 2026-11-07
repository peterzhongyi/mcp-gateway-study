"""
Multi-host benchmark runs on AWS EC2 for the MCP gateway study.

Topology: a gateway host (gateway + load generator in one process, as in the local runs) and an
upstream host (upstream_agent.py launching upstream_http_server.py on demand). Network delay is
real; no RTT is emulated. Each layout runs as an independent stack, so layouts can run in parallel.

  python aws_bench.py up --stack same-az --layout same-az [--type c7i.large]
  python aws_bench.py run --stack same-az --label aws_same_az --exps all [--env "MCP_TRIALS=1"]
  python aws_bench.py status
  python aws_bench.py down --stack same-az     # terminate one stack
  python aws_bench.py down --all               # terminate everything and delete key pairs / security groups

All resources carry the tag Project=mcp-bench. Uses the AWS CLI profile `mcp-bench`.
"""

import argparse
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.join(HERE, "..", "mcp_cloud_proxy")
RESULTS = os.path.join(CODE, "results", "aws")
KNOWN_HOSTS = os.path.join(HERE, "known_hosts")
AWS = os.environ.get("AWS_CLI", r"C:\Program Files\Amazon\AWSCLIV2\aws.exe")
PROFILE = "mcp-bench"
HOME_REGION = "us-east-1"
TAG = "mcp-bench"
AMI_PARAM = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
CODE_FILES = ["gateway_middleware.py", "hires_timer.py", "http11.py", "proxy_transports.py", "run_benchmarks.py",
              "security_suite.py", "upstream_agent.py", "upstream_http_server.py", "upstream_mcp_server.py"]
REGIONS = ["us-east-1", "us-east-2"]
# layout -> ((gateway region, AZ), (upstream region, AZ))
LAYOUTS = {
    "same-az": (("us-east-1", "us-east-1a"), ("us-east-1", "us-east-1a")),
    "cross-az": (("us-east-1", "us-east-1a"), ("us-east-1", "us-east-1b")),
    "cross-region": (("us-east-1", "us-east-1a"), ("us-east-2", "us-east-2a")),
}

# Packages, plus kernel settings so the unpooled transport's closed connections (TIME_WAIT) do not
# exhaust ephemeral ports at saturation.
USER_DATA = """#cloud-config
package_update: true
packages: [python3-numpy, python3-scipy]
runcmd:
  - sysctl -w net.ipv4.ip_local_port_range="1024 65000"
  - sysctl -w net.ipv4.tcp_tw_reuse=1
  - sysctl -w net.core.somaxconn=4096
"""


def aws(region: str, *args: str, parse: bool = True):
    cmd = [AWS, *args, "--profile", PROFILE, "--region", region, "--output", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"aws {' '.join(args[:2])} ({region}) failed:\n{r.stderr.strip()}")
    return json.loads(r.stdout) if parse and r.stdout.strip() else r.stdout


def key_path(region: str) -> str:
    return os.path.join(HERE, "mcp-bench.pem" if region == HOME_REGION else f"mcp-bench-{region}.pem")


def state_path(stack: str) -> str:
    return os.path.join(HERE, f"state_{stack}.json")


def load_state(stack: str) -> dict:
    p = state_path(stack)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(stack: str, s: dict) -> None:
    with open(state_path(stack), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)


def tag_spec(kind: str, name: str, stack: str = "") -> str:
    tags = f"{{Key=Project,Value={TAG}}},{{Key=Name,Value={name}}}"
    if stack:
        tags += f",{{Key=Stack,Value={stack}}}"
    return f"ResourceType={kind},Tags=[{tags}]"


# ----------------------------------------------------------------------------------------- ssh
def ssh_base(host: dict) -> list:
    return ["ssh", "-i", host["key"], "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
            "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30", "-o", "BatchMode=yes", f"ubuntu@{host['public']}"]


def ssh(host: dict, command: str, check: bool = True) -> str:
    r = subprocess.run(ssh_base(host) + [command], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"ssh {host['public']} failed ({r.returncode}): {command}\n{(r.stderr or '').strip()}")
    return r.stdout


def scp(key: str, src: str, dst: str) -> None:
    r = subprocess.run(["scp", "-q", "-i", key, "-o", "StrictHostKeyChecking=accept-new",
                        "-o", f"UserKnownHostsFile={KNOWN_HOSTS}", "-o", "BatchMode=yes", src, dst],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"scp {src} -> {dst} failed: {r.stderr.strip()}")


def wait_ssh(host: dict, timeout_s: int = 300) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if subprocess.run(ssh_base(host) + ["true"], capture_output=True, text=True).returncode == 0:
            return
        time.sleep(5)
    raise SystemExit(f"ssh to {host['public']} not ready after {timeout_s}s")


# ----------------------------------------------------------------------------------------- up
def ensure_key(region: str) -> str:
    path = key_path(region)
    if os.path.exists(path):
        return path
    out = aws(region, "ec2", "create-key-pair", "--key-name", TAG, "--key-type", "ed25519",
              "--tag-specifications", tag_spec("key-pair", TAG))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(out["KeyMaterial"])
    if sys.platform == "win32":  # OpenSSH refuses keys readable by other users
        subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", f"{os.environ['USERNAME']}:R"],
                       capture_output=True, check=True)
    else:
        os.chmod(path, 0o600)
    return path


def find_sg(region: str):
    groups = aws(region, "ec2", "describe-security-groups", "--filters", f"Name=group-name,Values={TAG}-sg")["SecurityGroups"]
    return groups[0]["GroupId"] if groups else None


def ensure_sg(region: str) -> str:
    sg = find_sg(region)
    if sg:
        return sg
    vpc = aws(region, "ec2", "describe-vpcs", "--filters", "Name=is-default,Values=true")["Vpcs"][0]["VpcId"]
    sg = aws(region, "ec2", "create-security-group", "--group-name", f"{TAG}-sg", "--vpc-id", vpc,
             "--description", "MCP gateway benchmark (SSH from operator, all TCP within group)",
             "--tag-specifications", tag_spec("security-group", f"{TAG}-sg"))["GroupId"]
    my_ip = urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10).read().decode().strip()
    aws(region, "ec2", "authorize-security-group-ingress", "--group-id", sg, "--protocol", "tcp", "--port", "22",
        "--cidr", f"{my_ip}/32")
    aws(region, "ec2", "authorize-security-group-ingress", "--group-id", sg, "--protocol", "tcp", "--port", "0-65535",
        "--source-group", sg)
    return sg


def launch(stack: str, role: str, region: str, az: str, itype: str, sg: str) -> str:
    ami = aws(region, "ssm", "get-parameters", "--names", AMI_PARAM)["Parameters"][0]["Value"]
    subnet = aws(region, "ec2", "describe-subnets", "--filters", f"Name=availability-zone,Values={az}",
                 "Name=default-for-az,Values=true")["Subnets"][0]["SubnetId"]
    ud_path = os.path.join(HERE, "user_data.yaml")
    with open(ud_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(USER_DATA)
    name = f"{TAG}-{stack}-{role}"
    out = aws(region, "ec2", "run-instances", "--image-id", ami, "--instance-type", itype, "--key-name", TAG,
              "--security-group-ids", sg, "--subnet-id", subnet, "--count", "1",
              "--user-data", f"file://{ud_path}",
              "--instance-initiated-shutdown-behavior", "terminate",
              "--block-device-mappings", "DeviceName=/dev/sda1,Ebs={VolumeSize=10,VolumeType=gp3,DeleteOnTermination=true}",
              "--tag-specifications", tag_spec("instance", name, stack), tag_spec("volume", name, stack))
    return out["Instances"][0]["InstanceId"]


def package_code() -> str:
    path = os.path.join(HERE, "code.tar.gz")
    with tarfile.open(path, "w:gz") as tar:
        for fn in CODE_FILES:
            tar.add(os.path.join(CODE, fn), arcname=fn)
    return path


def upload_code(host: dict, tarball: str) -> None:
    scp(host["key"], tarball, f"ubuntu@{host['public']}:/tmp/code.tar.gz")
    ssh(host, "mkdir -p ~/mcp/results && tar -xzf /tmp/code.tar.gz -C ~/mcp")


def cmd_up(a) -> None:
    state = load_state(a.stack)
    if state.get("gw"):
        raise SystemExit(f"stack {a.stack} already up (run `down --stack {a.stack}` first)")
    placement = dict(zip(("gw", "up"), LAYOUTS[a.layout]))
    state.update({"layout": a.layout, "type": a.type})
    for role, (region, az) in placement.items():
        key = ensure_key(region)
        iid = launch(a.stack, role, region, az, a.type, ensure_sg(region))
        state[role] = {"id": iid, "region": region, "key": key}
        save_state(a.stack, state)
    print(f"stack {a.stack}: launched gw={state['gw']['id']} up={state['up']['id']} ({a.type}, {a.layout})")
    for role in ("gw", "up"):
        h = state[role]
        aws(h["region"], "ec2", "wait", "instance-running", "--instance-ids", h["id"], parse=False)
        i = aws(h["region"], "ec2", "describe-instances", "--instance-ids", h["id"])["Reservations"][0]["Instances"][0]
        h.update({"public": i["PublicIpAddress"], "private": i["PrivateIpAddress"], "az": i["Placement"]["AvailabilityZone"]})
    save_state(a.stack, state)
    gw, up = state["gw"], state["up"]
    if gw["region"] != up["region"]:
        # Across regions the upstream is reached on its public address; admit only the gateway.
        aws(up["region"], "ec2", "authorize-security-group-ingress", "--group-id", ensure_sg(up["region"]),
            "--protocol", "tcp", "--port", "0-65535", "--cidr", f"{gw['public']}/32")
    tarball = package_code()
    for role in ("gw", "up"):
        h = state[role]
        print(f"provisioning {role} {h['public']} ({h['az']})", flush=True)
        wait_ssh(h)
        ssh(h, "cloud-init status --wait > /dev/null; python3 -c 'import numpy, scipy'")
        upload_code(h, tarball)
    print("ready:", json.dumps({r: {k: state[r][k] for k in ("public", "private", "az")} for r in ("gw", "up")}))


# ----------------------------------------------------------------------------------------- run
def cmd_run(a) -> None:
    state = load_state(a.stack)
    gw, up = state["gw"], state["up"]
    if a.sync:
        tarball = package_code()
        upload_code(gw, tarball)
        upload_code(up, tarball)
    up_addr = up["private"] if gw["region"] == up["region"] else up["public"]
    ssh(up, "pkill -f '[u]pstream_(agent|http_server)'", check=False)
    ssh(up, "cd ~/mcp && setsid -f python3 upstream_agent.py --port 7000 > agent.log 2>&1 < /dev/null")
    time.sleep(2)
    ssh(up, "pgrep -f '[u]pstream_agent' > /dev/null || (cat ~/mcp/agent.log; exit 1)")
    label = a.label
    env = (f"MCP_UPSTREAM_AGENT={up_addr}:7000 MCP_UPSTREAM_HOST={up_addr} MCP_RTTS=0 MCP_CONC_RTT=0 "
           f"MCP_OUT=results/{label}.json MCP_TESTBED_LABEL='aws {state['type']} {state['layout']} "
           f"{gw['az']}->{up['az']}' MCP_TESTBED_CPU=\"$(lscpu | sed -n 's/^Model name: *//p') "
           f"($(nproc) vCPU)\" MCP_TESTBED_RAM_GB=$(awk '/MemTotal/{{printf \"%.1f\", $2/1048576}}' /proc/meminfo) {a.env}")
    ssh(gw, f"cd ~/mcp && rm -f results/{label}.log && {env} setsid -f python3 -u run_benchmarks.py "
        f"{a.exps} > results/{label}.log 2>&1 < /dev/null")
    print(f"started {label} on gw {gw['public']} -> upstream {up_addr}; streaming log", flush=True)
    seen = 0
    while True:
        time.sleep(15)
        lines = ssh(gw, f"cat ~/mcp/results/{label}.log", check=False).splitlines()
        for line in lines[seen:]:
            print(line, flush=True)
        seen = len(lines)
        if ssh(gw, "pgrep -f '[r]un_benchmarks.py' > /dev/null && echo yes || echo no", check=False).strip() != "yes":
            break
    os.makedirs(RESULTS, exist_ok=True)
    for ext in ("json", "log"):
        try:
            scp(gw["key"], f"ubuntu@{gw['public']}:mcp/results/{label}.{ext}", os.path.join(RESULTS, f"{label}.{ext}"))
        except SystemExit as e:
            print(f"warning: {e}")
    print(f"results -> {os.path.join(RESULTS, label)}.json")


# ----------------------------------------------------------------------------------------- status / down
def tagged_instances(region: str) -> list:
    desc = aws(region, "ec2", "describe-instances", "--filters", f"Name=tag:Project,Values={TAG}",
               "Name=instance-state-name,Values=pending,running,stopping,stopped")
    return [i["InstanceId"] for r in desc["Reservations"] for i in r["Instances"]]


def cmd_status(_a) -> None:
    for region in REGIONS:
        print(f"{region}: running mcp-bench instances: {tagged_instances(region) or 'none'}")
    for fn in sorted(os.listdir(HERE)):
        if fn.startswith("state_"):
            print(fn, open(os.path.join(HERE, fn), encoding="utf-8").read().replace("\n", ""))


def terminate(region: str, ids: list) -> None:
    if ids:
        aws(region, "ec2", "terminate-instances", "--instance-ids", *ids)
        print(f"{region}: terminating {ids}", flush=True)
        aws(region, "ec2", "wait", "instance-terminated", "--instance-ids", *ids, parse=False)


def cmd_down(a) -> None:
    if not a.all:
        state = load_state(a.stack)
        by_region: dict = {}
        for role in ("gw", "up"):
            if state.get(role, {}).get("id"):
                by_region.setdefault(state[role]["region"], []).append(state[role]["id"])
        for region, ids in by_region.items():
            terminate(region, ids)
        if os.path.exists(state_path(a.stack)):
            os.remove(state_path(a.stack))
        print(f"stack {a.stack} down")
        return
    for region in REGIONS:
        terminate(region, tagged_instances(region))
        sg = find_sg(region)
        if sg:
            aws(region, "ec2", "delete-security-group", "--group-id", sg, parse=False)
        aws(region, "ec2", "delete-key-pair", "--key-name", TAG, parse=False)
        kp = key_path(region)
        if os.path.exists(kp):
            if sys.platform == "win32":
                subprocess.run(["icacls", kp, "/grant", f"{os.environ['USERNAME']}:F"], capture_output=True)
            os.remove(kp)
    for fn in os.listdir(HERE):
        if fn.startswith("state_") or fn == "known_hosts":
            os.remove(os.path.join(HERE, fn))
    print("all down; remaining:", {r: tagged_instances(r) or "none" for r in REGIONS})


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("up")
    p.add_argument("--stack", required=True)
    p.add_argument("--layout", choices=list(LAYOUTS), required=True)
    p.add_argument("--type", default="c7i.large")
    p = sub.add_parser("run")
    p.add_argument("--stack", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--exps", default="exp1 exp5")
    p.add_argument("--env", default="")
    p.add_argument("--sync", action="store_true", help="re-upload code before running")
    sub.add_parser("status")
    p = sub.add_parser("down")
    p.add_argument("--stack")
    p.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.cmd == "down" and not (a.all or a.stack):
        ap.error("down needs --stack or --all")
    {"up": cmd_up, "run": cmd_run, "status": cmd_status, "down": cmd_down}[a.cmd](a)


if __name__ == "__main__":
    main()
