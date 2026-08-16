# vpc.tf - "New VPC" path (KX's own module offers the same New-VPC/
# Existing-VPC choice - see variables.tf's vpc_id). All resources here are
# no-ops (count = 0) when var.vpc_id is set; local.vpc_id/private_subnet_ids/
# public_subnet_ids below resolve to whichever path is active so eks.tf
# never needs to know which one it's in.

resource "aws_vpc" "this" {
  count = local.use_existing_vpc ? 0 : 1

  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.common_tags, { Name = local.name })
}

resource "aws_internet_gateway" "this" {
  count = local.use_existing_vpc ? 0 : 1

  vpc_id = aws_vpc.this[0].id
  tags   = merge(local.common_tags, { Name = "${local.name}-igw" })
}

# One /20 public + one /19 private per AZ, carved out of the /16 - room for
# real node counts in the private ranges without needing a redesign later.
resource "aws_subnet" "public" {
  count = local.use_existing_vpc ? 0 : local.az_count

  vpc_id                  = aws_vpc.this[0].id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  map_public_ip_on_launch = true

  tags = merge(local.common_tags, {
    Name                     = "${local.name}-public-${local.azs[count.index]}"
    "kubernetes.io/role/elb" = "1"
  })
}

resource "aws_subnet" "private" {
  count = local.use_existing_vpc ? 0 : local.az_count

  vpc_id            = aws_vpc.this[0].id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 3, count.index + 2)

  tags = merge(local.common_tags, {
    Name                              = "${local.name}-private-${local.azs[count.index]}"
    "kubernetes.io/role/internal-elb" = "1"
  })
}

resource "aws_eip" "nat" {
  count  = local.use_existing_vpc ? 0 : local.nat_gateway_count
  domain = "vpc"
  tags   = merge(local.common_tags, { Name = "${local.name}-nat-${count.index}" })
}

# cost_optimized (nat_gateway_count = 1) puts every private subnet's route
# through the same single NAT gateway; ha/performance give each AZ its own,
# so one AZ's NAT outage doesn't cut egress for every other AZ too.
resource "aws_nat_gateway" "this" {
  count = local.use_existing_vpc ? 0 : local.nat_gateway_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = merge(local.common_tags, { Name = "${local.name}-nat-${count.index}" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  count = local.use_existing_vpc ? 0 : 1

  vpc_id = aws_vpc.this[0].id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this[0].id
  }
  tags = merge(local.common_tags, { Name = "${local.name}-public" })
}

resource "aws_route_table_association" "public" {
  count = local.use_existing_vpc ? 0 : local.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public[0].id
}

resource "aws_route_table" "private" {
  count = local.use_existing_vpc ? 0 : local.az_count

  vpc_id = aws_vpc.this[0].id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[count.index % local.nat_gateway_count].id
  }
  tags = merge(local.common_tags, { Name = "${local.name}-private-${local.azs[count.index]}" })
}

resource "aws_route_table_association" "private" {
  count = local.use_existing_vpc ? 0 : local.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

locals {
  vpc_id             = local.use_existing_vpc ? var.vpc_id : aws_vpc.this[0].id
  private_subnet_ids = local.use_existing_vpc ? var.existing_private_subnet_ids : aws_subnet.private[*].id
  public_subnet_ids  = local.use_existing_vpc ? var.existing_public_subnet_ids : aws_subnet.public[*].id
}
