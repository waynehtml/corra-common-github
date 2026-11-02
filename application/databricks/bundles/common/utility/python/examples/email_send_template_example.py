# Databricks notebook source
# DBTITLE 1,Inlude common routine for examples
# MAGIC %run ./__common__

# COMMAND ----------

# DBTITLE 1,Import Common Python Packages
import posixpath
import pandas as pd
from oeds.core.env_utils import ENV
import oeds.core.log_utils as logutils
import oeds.core.file_utils as fileutils
from oeds.domain.email_client import EmailClient

# COMMAND ----------

# DBTITLE 1,Set up and get logger
logutils.setup_logger(level="DEBUG")
logger = logutils.get_logger(ENV.CURRENT_NOTEBOOK_NAME) 

# COMMAND ----------

oeds_email_service_url = dbutils.secrets.get(scope="lake-kv-oedataservices-scope", key="oeds-email-service-url")
print(f"oeds_email_service_url: {oeds_email_service_url}")

# COMMAND ----------

# DBTITLE 1,Initiate email client
email_client = EmailClient(oeds_email_service_url)

# COMMAND ----------

# template file in workspace
template_file = posixpath.join(ENV.CURRENT_DIR, "./files/template.html")
print(f"template_file: {template_file}")
template_url = fileutils.workspace_url(template_file) 
print(f"template_url: {template_url}")



# table data in email body
df = pd.DataFrame({"Name": ["Alice", "Bob"], "Age": [25, 30]})
html_table = email_client.dataframe_to_html_table(df)
print(f"HTML table:\n{html_table}")

# prepare for replament variables
variables = {
    "user": "Katherine",
    "sales": "$15,000",
    "email" : "weidu@bank-banque-canada.ca",
    "table" : html_table
}

# Preapare attachment
attachment_name = "report.csv"
attachment_content = pd.DataFrame({"Name": ["Alice", "Bob"], "Age": [25, 30]})

# COMMAND ----------

# Send email with embedded subject and recipient
email_client.send_email_from_template(
    template_url,variables,
    attachment1_name=attachment_name,
    attachment1_content=attachment_content)

# COMMAND ----------

# Send email with overrided subject and recipients
email_client.send_email_from_template(
    template_url,variables,
    attachment1_name=attachment_name,
    attachment1_content=attachment_content,
    recipients="weidu@bank-banque-canada.ca,duwe@bank-banque-canada.ca",
    subject="Weekly Report1 $user")
