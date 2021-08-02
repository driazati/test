# `aws_od_cli`

`aws_od_cli` lets you easily spin up / down EC2 instances geared for PyTorch development. The images come preinstalled with:

* VSCode server + extensions (GitLens, clangd, Python, cmake)
* clangd
* PyTorch built from master in `~/pytorch` + developer tools (linters, test packages)
* Various `apt` packages
* CUDA for GPU instances

## Usage

`aws_od_cli` requires you have your credentials configured in some way that `boto3` can access (e.g. environment variables or `~/.aws/`).

```bash
export AWS_SECRET_ACCESS_KEY=abc123
export AWS_ACCESS_KEY_ID=abc123
```

1. Install the CLI

    ```bash
    git clone https://github.com/driazati/test
    cd test/od/aws_od_cli
    pip install -e .
    aws_od_cli --help
    ```

2. Create an instance. You will automatically be sent into an SSH session once the instance has been created (usually takes around 1 minute).

    ```bash
    # Basic CPU instance
    aws_od_cli create

    # Basic GPU instance
    aws_od_cli create --gpu
    ```

    **When the SSH session exits, the machine will be deprovisioned!** If you want to save your work, you should push it to a git branch or use the `--no-rm` flag. When exiting with `--no-rm`, your machine will stop so it is not incurring cost, but your work will be saved on an EBS volume which can be used to restart the machine.

### Other Tasks

#### List your instances

    ```bash
    aws_od_cli list
    ```

#### Stop a running instance

    ```bash
    # Terminate instance (delete volume)
    aws_od_cli stop --name <instance name>

    # Stop instance (keep volume around for later)
    aws_od_cli stop --name <instance name> --action stop
    ```

#### Log into an instance

    ```bash
    # If you have only one instance running it will be used automatically
    aws_od_cli ssh

    aws_od_cli ssh --name <instance name>

    # The same applies to starting VSCode
    aws_od_cli vscode

    aws_od_cli vscode --name <instance name>
    ```

#### Add files to be copied over when the instance starts

    ```bash
    # Show configs
    aws_od_cli configs --list

    # Add a config
    aws_od_cli configs --add ~/.tmux.conf
    ```

#### Export prior run logs for debugging

    ```bash
    aws_od_cli rage --number 1
    ```
