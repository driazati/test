packer {
  required_plugins {
    amazon = {
      version = ">= 0.0.2"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

source "amazon-ebs" "ubuntu" {
  ami_name      = "learn-packer-linux-aws"
  force_deregister = true
  instance_type = "c5a.4xlarge"
  region        = "us-west-2"

  # ubuntu 20.04 server
  source_ami = "ami-03d5c68bab01f3496"
  ssh_username = "ubuntu"

  launch_block_device_mappings {
    device_name = "/dev/sda1"
    volume_size = 50
    delete_on_termination = true
  }
}

build {
  sources = [
    "source.amazon-ebs.ubuntu"
  ]


  provisioner "shell" {
    environment_vars = [
      "FOO=hello world",
    ]
    scripts = [
      "setup.sh",
    ]
  }
}