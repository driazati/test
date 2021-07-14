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
  instance_type = "t2.micro"
  region        = "us-west-2"

  # ubuntu 20.04 server
  source_ami = "ami-03d5c68bab01f3496"
  ssh_username = "ubuntu"
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

# checkout PyTorch
# install build-essential, clang
# build PyTorch


# how to provision SSH key for only the current user?
# 