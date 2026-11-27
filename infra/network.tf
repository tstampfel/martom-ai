# VPC with two public subnets (Fargate tasks) and two private subnets (internal ALB, RDS).
#
# No NAT gateway: it would cost ~$35/month on its own. Fargate tasks run in the public
# subnets with a public IP so they can reach ECR, Secrets Manager, CloudWatch, OLX, Claude,
# Cohere and LangSmith, while their security groups accept no traffic from the internet
# (only the load balancer may reach the API port). The internal ALB and RDS stay in private
# subnets with no route to the internet; CloudFront reaches the ALB through a VPC origin.
# The internet gateway is also required for CloudFront VPC origins. The ALB and the RDS
# subnet group both need two availability zones.

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  prefix = "${var.name}-${var.environment}"
  azs    = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = local.prefix }
}

# Take every rule off the default security group so nothing uses it by accident.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = local.prefix }
}

resource "aws_subnet" "public" {
  count = 2

  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  # 10.40.0.0/24, 10.40.1.0/24
  cidr_block = cidrsubnet(var.vpc_cidr, 8, count.index)
  # Public IPs are given per task (assign_public_ip in ecs.tf), not to everything here.
  map_public_ip_on_launch = false

  tags = { Name = "${local.prefix}-public-${local.azs[count.index]}" }
}

resource "aws_subnet" "private" {
  count = 2

  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  # 10.40.10.0/24, 10.40.11.0/24
  cidr_block = cidrsubnet(var.vpc_cidr, 8, count.index + 10)

  tags = { Name = "${local.prefix}-private-${local.azs[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.prefix}-public" }
}

resource "aws_route_table_association" "public" {
  count = 2

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Private subnets get a route table with only the built-in local route: no internet.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${local.prefix}-private" }
}

resource "aws_route_table_association" "private" {
  count = 2

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
