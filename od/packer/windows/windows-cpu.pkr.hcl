packer {
  required_plugins {
    amazon = {
      version = ">= 0.0.2"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

source "amazon-ebs" "windows" {
  ami_name      = "pytorch-ondemand-windows-cpu"
  force_deregister = true
  instance_type = "c5a.4xlarge"
  region        = "us-west-2"
  communicator = "winrm"
  winrm_username = "Administrator"
  winrm_use_ssl = true
  winrm_insecure = true
  user_data_file = "winrm.ps1"

  force_delete_snapshot = true
  snapshot_tags = {
    "ondemand": "gha-packer"
  }
  tags = {
    "ondemand": "gha-packer"
  }

  # Microsoft Windows Server 2019 Base for us-west-2
  # This works on the console for getting the RDP password, maybe the winrm.ps1 script is messing things up?
  source_ami = "ami-0e9172b6cfc14e8d2"
  # ssh_username = "Administrator"

  launch_block_device_mappings {
    device_name = "/dev/sda1"
    volume_size = 100
    delete_on_termination = true
  }
}

build {
  sources = [
    "source.amazon-ebs.windows"
  ]

  # provisioner "powershell" {
  #   execution_policy = "unrestricted"
  #   scripts = [
  #     "${path.root}/init.ps1",
  #   ]
  # }
}