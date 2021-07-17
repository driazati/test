source "amazon-ebs" "ubuntu" {
  ami_name      = "pytorch-ondemand-ami-gpu"
  force_deregister = true
  instance_type = "g4dn.8xlarge"
  region        = "us-west-2"

  force_delete_snapshot = true
  snapshot_tags = {
    "ondemand": "gha-packer"
    "type": "gpu"
  }
  tags = {
    "ondemand": "gha-packer"
    "type": "gpu"
  }

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
    scripts = [
      "setup.sh",
      "gpu.sh",
      "build.sh"
    ]
  }
}