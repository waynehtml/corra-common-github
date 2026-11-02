config_template = """
{
  "pipeline": "corra",
  "tasks": [
    {
      "id": "download",
      "handler": "oedis.handler.database.download",
      "params": {
        "requests": [
          {
            "id": "[[ database1.source_table ]]",
            "select_sql": "SELECT * FROM [[ database1.source_table ]]",
            "format": "parquet"
          }
        ]
      }
    },
    {
      "id": "upload",
      "handler": "oedis.handler.database.upload",
      "params": {
        "requests": [
          {
            "id": "[[ database1.source_table ]]",
            "table": "[[ database1.target_table ]]",
            "select_sql": "select * from {source}",
            "create_sql": "CREATE TABLE IF NOT EXISTS [[ database1.target_table ]] (\\n{columns}\\n) USING DELTA",
            "write": {
              "mode": "overwrite"
            }
          }
        ]
      }
    }
  ]
}

""".strip()

config_params = {
    "database1": {
        "source_table": "dev_ext_ora_habs.gsmadmin_internal.duplicated_table",
        "target_table": "dev_application_cat.oedisint_db.duplicated_table",
    }
}

service_params = {
  
}
