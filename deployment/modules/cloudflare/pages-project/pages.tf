module "pages_project" {
  source = "git::https://github.com/immich-app/devtools.git//tf/shared/modules/cloudflare-pages-project?ref=main"

  cloudflare_api_token  = local.api_token
  cloudflare_account_id = local.account_id

  app_name = var.app_name
  env      = var.env
  domain   = "futo.org"
}

output "pages_project" {
  value = module.pages_project.pages_project
}
