# bastion.tf - see terraform/aws/bastion.tf's own header comment for why
# this exists (reaching things that only listen inside the VNet, e.g.
# mounting the Lustre filesystem directly to inspect it) despite the AKS
# API server already being reachable from aks_api_access_cidrs directly.

resource "azurerm_network_security_group" "bastion" {
  name                = "${local.name}-bastion-nsg"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags

  security_rule {
    name                       = "AllowSSH"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = var.bastion_access_cidr
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "bastion" {
  count = local.use_existing_vnet ? 0 : 1

  subnet_id                 = azurerm_subnet.bastion[0].id
  network_security_group_id = azurerm_network_security_group.bastion.id
}

resource "azurerm_public_ip" "bastion" {
  name                = "${local.name}-bastion-ip"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.common_tags
}

resource "azurerm_network_interface" "bastion" {
  name                = "${local.name}-bastion-nic"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = local.use_existing_vnet ? var.existing_subnet_id : azurerm_subnet.bastion[0].id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.bastion.id
  }
}

resource "tls_private_key" "bastion" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "azurerm_linux_virtual_machine" "bastion" {
  name                  = "${local.name}-bastion"
  location              = azurerm_resource_group.this.location
  resource_group_name   = azurerm_resource_group.this.name
  size                  = "Standard_B1s" # a jump host, not a workload - no reason for anything bigger
  admin_username        = "azureuser"
  network_interface_ids = [azurerm_network_interface.bastion.id]
  tags                  = local.common_tags

  admin_ssh_key {
    username   = "azureuser"
    public_key = tls_private_key.bastion.public_key_openssh
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }
}
