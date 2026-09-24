data "terraform_remote_state" "futo_api_keys" {
  backend = "pg"

  config = {
    conn_str    = var.tf_state_postgres_conn_str
    schema_name = "prod_cloudflare_futo_api_keys"
  }
}

data "terraform_remote_state" "cloudflare_pages_project" {
  backend = "pg"

  config = {
    conn_str    = var.tf_state_postgres_conn_str
    schema_name = "cloudflare_pages_project_futo_org_${replace(var.app_name, "-", "_")}_${var.env}"
  }
}

locals {
  account_id = data.terraform_remote_state.futo_api_keys.outputs.cloudflare_account_id
  api_token  = data.terraform_remote_state.futo_api_keys.outputs.survey_deploy_token
}
