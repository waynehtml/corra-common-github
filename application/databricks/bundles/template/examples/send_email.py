# Databricks notebook source
# MAGIC %run ../common

# COMMAND ----------

# Standard libraries
import logging

import pandas as pd

# Get logger
logger = logging.getLogger(__name__)

# Get job context
job_ctx = get_job_context()
logger.debug(f"job_ctx: {job_ctx}")

# COMMAND ----------

# Preview the configured email registry entry resolved from the packaged config.
email_definition = email_manager.get_email_registry("failure")
logger.info(f"email_definition: {email_definition}")
print("resolved recipients:", email_definition["recipients"])
print("template:", email_definition["template"])

# COMMAND ----------

# Build sample variables for the packaged failure template.
variables = {
    "ENV": job_ctx.get("env", "dev").upper(),
    "audit_skey": "123456789",
    "code": "EXAMPLE_FAILURE",
    "state": "FAILED",
    "message": "This is a template bundle example email.",
}

# Optional attachment content can be a pandas DataFrame or a string.
attachment_content = pd.DataFrame(
    [
        {
            "step": "extract",
            "status": "completed",
            "details": "Example upstream task finished successfully.",
        },
        {
            "step": "load",
            "status": "failed",
            "details": "Example validation failed before publish.",
        },
    ]
)

print("variables:", variables)
print("attachment rows:", len(attachment_content))

# COMMAND ----------

# Send the packaged failure email template using the configured job recipients.
email_manager.send_email(
    "failure",
    variables=variables,
    attachment1_name="email-example-details.csv",
    attachment1_content=attachment_content,
)
