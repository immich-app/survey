terraform {
  source = "."

  extra_arguments custom_vars {
    commands = get_terraform_commands_that_need_vars()
  }
}

include {
  path = find_in_parent_folders("state.hcl")
}

locals {
  env      = get_env("TF_VAR_env")
  stage    = get_env("TF_VAR_stage")
  app_name = replace(get_env("TF_VAR_app_name"), "-", "_")
}

remote_state {
  backend = "pg"

  config = {
    conn_str    = get_env("TF_VAR_tf_state_postgres_conn_str")
    schema_name = "cloudflare_futo_org_${local.app_name}_${local.env}${local.stage}"
  }
}

dependencies {
  paths = [
    "../pages-project",
  ]
}

errors {
  retry "pages_domain_already_added" {
    retryable_errors   = [".*You have already added this custom domain.*"]
    max_attempts       = 2
    sleep_interval_sec = 10
  }
}
