module "static_pages" {
  source = "git::https://github.com/immich-app/devtools.git//tf/shared/modules/cloudflare-pages?ref=main"

  cloudflare_api_token  = local.api_token
  cloudflare_account_id = local.account_id

  pages_project = data.terraform_remote_state.cloudflare_pages_project.outputs.pages_project

  app_name = var.subdomain
  stage    = var.stage
  env      = var.env
  domain   = "futo.org"
}

module "domain" {
  source = "git::https://github.com/immich-app/devtools.git//tf/shared/modules/domain?ref=main"

  app_name = var.subdomain
  stage    = var.stage
  env      = var.env
  domain   = "futo.org"
}

locals {
  pages_project_name = data.terraform_remote_state.cloudflare_pages_project.outputs.pages_project.name
}

# The Pages domain API can error on an attach that succeeded, leaving the domain outside state; adopt it instead of failing on "already added".
data "http" "pages_domains" {
  url             = "https://api.cloudflare.com/client/v4/accounts/${local.account_id}/pages/projects/${local.pages_project_name}/domains"
  request_headers = { Authorization = "Bearer ${local.api_token}" }

  lifecycle {
    postcondition {
      condition     = self.status_code == 200 && try(jsondecode(self.response_body).success, false)
      error_message = "Listing the Pages project's domains failed (HTTP ${self.status_code}); refusing to guess whether to adopt the domain."
    }
  }
}

import {
  for_each = contains([for d in jsondecode(data.http.pages_domains.response_body).result : d.name], module.domain.fqdn) ? toset([module.domain.fqdn]) : toset([])
  to       = module.static_pages.cloudflare_pages_domain.pages_domain
  id       = "${local.account_id}/${local.pages_project_name}/${each.value}"
}

output "pages_branch" {
  value = module.static_pages.pages_branch
}

output "subdomain" {
  value = module.static_pages.branch_subdomain
}

output "pages_branch_subdomain" {
  value = module.static_pages.pages_branch_subdomain
}

output "pages_project_name" {
  value = module.static_pages.pages_project_name
}
