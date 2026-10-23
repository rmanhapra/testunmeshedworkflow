import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def run(command):
    """Run a command and stop if it fails."""
    subprocess.run(command, check=True)


def common_file(folder, filename):
    """Find a file inside its expected common directory."""
    base = Path(folder).resolve()
    file = (base / filename).resolve()

    # Prevent a filename from pointing outside this directory.
    file.relative_to(base)

    if not file.is_file():
        raise ValueError(f"File does not exist: {file}")

    return file


def main():
    # --------------------------------------------------
    # 1. Read and check the deployment plan.
    # --------------------------------------------------
    with open("deployment.json") as file:
        plan = json.load(file)

    namespace = plan["namespace"]

    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", namespace)
        or namespace == "common"
    ):
        raise ValueError("Invalid namespace")

    namespace_folder = Path("prod") / namespace

    if not namespace_folder.is_dir():
        raise ValueError(
            f"Namespace directory does not exist: {namespace_folder}"
        )

    calendars = plan.get("calendars", [])
    variables = plan.get("variables", [])

    # Check every selected file before changing the server.
    for calendar in calendars:
        if calendar["action"] not in ("add", "update"):
            raise ValueError(
                "Calendar action must be add or update"
            )

        common_file(
            "prod/common/calendar",
            calendar["file"],
        )

    for variable in variables:
        file = common_file(
            "prod/common/variables",
            variable["file"],
        )

        # Check that the variable file contains valid JSON.
        json.loads(file.read_text())

    # --------------------------------------------------
    # 2. Update the selected calendars.
    # --------------------------------------------------
    for calendar in calendars:
        file = common_file(
            "prod/common/calendar",
            calendar["file"],
        )

        print(
            f"Calendar: {calendar['action']} {calendar['name']}",
            flush=True,
        )

        run([
            "unmeshed",
            "calendar",
            calendar["action"],
            calendar["name"],
            "-f",
            str(file),
        ])

    # --------------------------------------------------
    # 3. Update the selected variables.
    # --------------------------------------------------
    server_url = os.environ["UNMESHED_URL"].rstrip("/")
    token = os.environ["UNMESHED_TOKEN"]

    for variable in variables:
        file = common_file(
            "prod/common/variables",
            variable["file"],
        )

        payload = {
            "name": variable["name"],
            "value": json.loads(file.read_text()),
        }

        print(
            f"Updating variable: {variable['name']}",
            flush=True,
        )

        with tempfile.TemporaryDirectory() as directory:
            payload_file = Path(directory) / "payload.json"
            payload_file.write_text(json.dumps(payload))
            payload_file.chmod(0o600)

            print(f"payload {payload}")

            result = subprocess.run(
                [
                    "curl",
                    "--fail-with-body",
                    "--silent",
                    "--show-error",
                    "--connect-timeout", "15",
                    "--max-time", "120",
                    "--request", "POST",
                    f"{server_url}/api/globalVariables",
                    "--header", f"Authorization: Bearer {token}",
                    "--header", "Content-Type: application/json",
                    "--header", "accept: */*",
                    "--data-binary", f"@{payload_file}",
                ],
                capture_output=True,
            )

       

        if result.returncode != 0:
            raise RuntimeError(
                f"Variable update failed: {variable['name']}: {result}"
            )

        # Handle an application error returned with HTTP 200.
        if result.stdout.strip():
            try:
                response = json.loads(result.stdout)
            except ValueError:
                response = None

            if isinstance(response, dict):
                if (
                    response.get("success") is False
                    or response.get("error")
                ):
                    raise RuntimeError(
                        f"Variable API reported failure: "
                        f"{variable['name']}"
                    )

    # --------------------------------------------------
    # 4. Upload the complete namespace directory.
    # --------------------------------------------------
    print(
        f"Uploading namespace: {namespace}",
        flush=True,
    )

    run([
        "unmeshed",
        "update-remote",
        f"./prod/{namespace}",
        "-n",
        namespace,
    ])

    print("Deployment completed successfully.", flush=True)


if __name__ == "__main__":
    main()