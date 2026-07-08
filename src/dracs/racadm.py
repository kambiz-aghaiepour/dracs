import subprocess  # nosec


def run_racadm_ssh(
    idrac_fqdn: str, username: str, password: str, racadm_args: list
) -> subprocess.CompletedProcess:
    """Run a single racadm command on an iDRAC over SSH using sshpass."""
    cmd = [
        "sshpass",
        "-p",
        password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=30",
        "-o",
        "BatchMode=no",
        f"{username}@{idrac_fqdn}",
        "racadm",
    ] + racadm_args
    return subprocess.run(  # nosec # nosemgrep
        cmd, capture_output=True, text=True, timeout=60
    )
