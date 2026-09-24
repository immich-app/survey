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
