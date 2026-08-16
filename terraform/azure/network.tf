# network.tf - "New VNet" path, mirroring terraform/aws/vpc.tf's own New-
# VPC/Existing-VPC choice. Azure doesn't split public/private subnets the
# same way AWS does (no per-subnet route-to-IGW-vs-NAT distinction) - a
# single subnet plus an NSG and a NAT Gateway for egress is the idiomatic
# Azure equivalent of "private subnet with NAT egress, no direct inbound."

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.common_tags
}

resource "azurerm_virtual_network" "this" {
  count = local.use_existing_vnet ? 0 : 1

  name                = "${local.name}-vnet"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = [var.vnet_cidr]
  tags                = local.common_tags
}

resource "azurerm_subnet" "nodes" {
  count = local.use_existing_vnet ? 0 : 1

  name                 = "${local.name}-nodes"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this[0].name
  address_prefixes     = [cidrsubnet(var.vnet_cidr, 4, 0)]
}

resource "azurerm_subnet" "bastion" {
  count = local.use_existing_vnet ? 0 : 1

  name                 = "${local.name}-bastion"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this[0].name
  address_prefixes     = [cidrsubnet(var.vnet_cidr, 8, 240)]
}

resource "azurerm_public_ip" "nat" {
  count = local.use_existing_vnet ? 0 : 1

  name                = "${local.name}-nat-ip"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = var.cluster_profile == "cost_optimized" ? null : [local.zones[0]]
  tags                = local.common_tags
}

resource "azurerm_nat_gateway" "this" {
  count = local.use_existing_vnet ? 0 : 1

  name                = "${local.name}-nat"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku_name            = "Standard"
  tags                = local.common_tags
}

resource "azurerm_nat_gateway_public_ip_association" "this" {
  count = local.use_existing_vnet ? 0 : 1

  nat_gateway_id       = azurerm_nat_gateway.this[0].id
  public_ip_address_id = azurerm_public_ip.nat[0].id
}

resource "azurerm_subnet_nat_gateway_association" "nodes" {
  count = local.use_existing_vnet ? 0 : 1

  subnet_id      = azurerm_subnet.nodes[0].id
  nat_gateway_id = azurerm_nat_gateway.this[0].id
}

resource "azurerm_network_security_group" "nodes" {
  count = local.use_existing_vnet ? 0 : 1

  name                = "${local.name}-nodes-nsg"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags

  security_rule {
    name                       = "DenyAllInbound"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "nodes" {
  count = local.use_existing_vnet ? 0 : 1

  subnet_id                 = azurerm_subnet.nodes[0].id
  network_security_group_id = azurerm_network_security_group.nodes[0].id
}

locals {
  node_subnet_id = local.use_existing_vnet ? var.existing_subnet_id : azurerm_subnet.nodes[0].id
}
