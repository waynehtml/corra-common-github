"""Email client helpers for sending Logic App requests and formatting payloads."""

import requests
from typing import List, Dict, Union, Optional, Tuple, Any
import base64
import io
import re
from string import Template
import logging
import pandas as pd
import oeds.core.file_utils as fileutils

logger = logging.getLogger(__name__)

class EmailSendError(RuntimeError):
    """Raised when sending email via Logic App fails."""
    pass


class EmailClient:
    """Lightweight client for sending emails through Azure Logic Apps."""

    def __init__(self, logicapp_url: str):
        if not isinstance(logicapp_url, str) or not logicapp_url.strip():
            raise ValueError("logicapp_url is required.")
        self.logicapp_url = logicapp_url.strip()

    def _normalize_recipients(self, to: Union[str, List[str]]) -> Union[str, List[str]]:
        """Normalize recipient input into a list of email strings.

        Args:
            to: Recipient specification as a string with separators or a list of strings.

        Returns:
            List of recipient email addresses.

        Raises:
            ValueError: If recipients are empty or of an unsupported type.
        """
        if isinstance(to, str):
            to_str = to.strip()
            if not to_str:
                raise ValueError("Recipient email cannot be empty.")
            to_list = [r.strip() for r in re.split(r"[;,]", to_str) if r.strip()]
            return to_list
        elif isinstance(to, list):
            to_list = [x.strip() for x in to if isinstance(x, str) and x.strip()]
            if not to_list:
                raise ValueError("Recipient list cannot be empty.")
            return to_list
        else:
            raise ValueError("`to` must be a string or a list of strings.")

    def _validate_attachments(self, atts: Optional[List[Dict]]) -> List[Dict[str, str]]:
        """Validate Logic App attachment structures.

        Args:
            atts: Attachments as a list of dictionaries with ``Name`` and ``ContentBytes`` keys.

        Returns:
            Validated list of attachments.

        Raises:
            ValueError: If any attachment is missing required keys or is not a dictionary.
        """
        atts = atts or []
        validated = []
        for i, a in enumerate(atts, 1):
            if not isinstance(a, dict):
                raise ValueError(f"Attachment #{i} must be a dict.")
            name = a.get("Name")
            b64 = a.get("ContentBytes")
            if not (isinstance(name, str) and name):
                raise ValueError(f"Attachment #{i} missing 'Name'.")
            if not (isinstance(b64, str) and b64):
                raise ValueError(f"Attachment #{i} missing 'ContentBytes'.")
            validated.append({"Name": name, "ContentBytes": b64})
        return validated

    def send_email(
        self,
        recipients: Union[str, List[str]],
        subject: str,
        body: str,
        attachments: Optional[List[Dict[str, str]]] = None,
        timeout: int = 30,
    ) -> None:
        """Send an email request to the Logic App.

        Args:
            recipients: Email addresses as a string or list.
            subject: Email subject line.
            body: HTML body content.
            attachments: Optional Logic App attachment dictionaries.
            timeout: Timeout in seconds for the HTTP request.

        Returns:
            None. The email request is issued as a side effect.

        Raises:
            EmailSendError: If the Logic App call fails or returns a non-success status.
        """
        recips = self._normalize_recipients(recipients)
        atts = self._validate_attachments(attachments)

        payload = {
            "to": recips,
            "subject": subject,
            "htmlBody": body,
            "attachments": atts,
        }

        headers = {"Content-Type": "application/json"}

        try:
            resp = requests.post(self.logicapp_url, headers=headers, json=payload, timeout=timeout)
            if not (200 <= resp.status_code < 300):
                raise EmailSendError(f"Logic App returned {resp.status_code}: {resp.text}")
        except Exception as e:
            raise EmailSendError(f"Failed to send email via Logic App: {e}")

    def make_attachment(self, name: str, content: Any) -> Dict[str, str]:
        """Create a Logic App attachment dictionary from supported content.

        Supported content types include strings, pandas ``DataFrame`` objects, and PySpark
        ``DataFrame`` objects (converted to pandas). DataFrames are encoded as CSV with
        headers and no index.

        Args:
            name: Attachment filename (for example, ``"data.csv"``).
            content: String, pandas ``DataFrame``, or PySpark ``DataFrame`` to encode.

        Returns:
            Dictionary containing ``Name`` and base64-encoded ``ContentBytes``.

        Raises:
            ValueError: If ``name`` is empty or ``content`` is ``None``.
            TypeError: If ``content`` is an unsupported type.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Attachment 'name' must be a non-empty string.")
        if content is None:
            raise ValueError("content cannot be None.")

        # String input
        if isinstance(content, str):
            encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
            return {"Name": name, "ContentBytes": encoded}

        # pandas DataFrame input
        if isinstance(content, pd.DataFrame):
            buf = io.StringIO()
            content.to_csv(buf, index=False, header=True)
            encoded = base64.b64encode(buf.getvalue().encode("utf-8")).decode("utf-8")
            return {"Name": name, "ContentBytes": encoded}

        # PySpark DataFrame input
        try:
            from pyspark.sql import DataFrame as SparkDataFrame
            if isinstance(content, SparkDataFrame):
                pdf = content.toPandas()
                buf = io.StringIO()
                pdf.to_csv(buf, index=False, header=True)
                encoded = base64.b64encode(buf.getvalue().encode("utf-8")).decode("utf-8")
                return {"Name": name, "ContentBytes": encoded}
        except ImportError:
            pass  # pyspark not installed; skip Spark detection

        raise TypeError(
            f"Unsupported content type '{type(content).__name__}'. "
            "Expected string, pandas DataFrame, or PySpark DataFrame."
        )

    def dataframe_to_html_table(self, obj, table_class: str = "data-table") -> str:
        """Convert a pandas or PySpark DataFrame into an HTML table string.

        Args:
            obj: pandas ``DataFrame`` or PySpark ``DataFrame`` to render.
            table_class: CSS class names to apply to the ``<table>`` tag.

        Returns:
            HTML string containing the rendered table.

        Raises:
            TypeError: If the object is not a pandas or PySpark DataFrame.
        """

        # Detect and convert Spark DataFrame if needed
        if hasattr(obj, "toPandas") and hasattr(obj, "columns"):
            pdf = obj.toPandas()
        elif isinstance(obj, pd.DataFrame):
            pdf = obj
        else:
            raise TypeError("Expected a pandas.DataFrame or pyspark.sql.DataFrame.")

        # Return simple HTML table (no index)
        return pdf.to_html(index=False, border=0, escape=True, classes=table_class)
    

    def render_email_template(self, template_html: str, variables: Dict[str, str]) -> Tuple[List[str], str, str]:
        """Render an HTML email template and extract metadata.

        The template may include ``<meta name="subject">`` and ``<meta name="recipient">``
        tags as well as ``$variable`` placeholders that are filled using ``string.Template``.

        Args:
            template_html: HTML template content.
            variables: Mapping of template variable names to substitution values.

        Returns:
            Tuple of recipients list, subject, and the fully rendered HTML.

        Raises:
            ValueError: If inputs are invalid or required metadata is missing.
        """
        if not template_html or not isinstance(template_html, str):
            raise ValueError("template_html must be a non-empty string.")
        if not isinstance(variables, dict):
            raise ValueError("variables must be a dict.")

        # Substitute placeholders using Python's built-in Template
        rendered_html = Template(template_html).safe_substitute(variables)

        # Extract <meta name="subject"> and <meta name="recipient">
        def _extract_meta(name: str) -> str:
            pattern = fr'<meta[^>]+name=["\']{name}["\'][^>]+content=["\']([^"\']+)["\']'
            match = re.search(pattern, rendered_html, re.IGNORECASE)
            return match.group(1).strip() if match else ""

        subject = _extract_meta("subject")
        recipient_raw = _extract_meta("recipient")

        # Parse comma- or semicolon-separated list into array
        recipients = []
        if recipient_raw:
            recipients = [r.strip() for r in re.split(r"[;,]", recipient_raw) if r.strip()]

        return recipients, subject, rendered_html
    

    def send_email_from_template(
        self,
        template_url: str,
        variables: Dict[str, object],
        recipients: Optional[Union[str, List[str]]] = None,
        subject: str = "",
        attachment1_name: Optional[str] = None,
        attachment1_content: Optional[object] = None,
        attachment2_name: Optional[str] = None,
        attachment2_content: Optional[object] = None,
        *,
        timeout: int = 30,
    ) -> None:
        """
        One-call convenience:
        - Load HTML template from Workspace, file:, or abfss:// path
        - Substitute $placeholders using `variables`
        - Extract <meta name="subject"> and <meta name="recipient"> (comma/semicolon-delimited)
        - Allow explicit `recipients` and `subject` to override template meta
        - Optionally include up to two attachments, each with (name, content):
            * name: filename (e.g., "report.csv")
            * content: str | pandas.DataFrame | PySpark DataFrame

        Raises:
            EmailSendError on any failure to compose or send.
        """
        try:
            if not isinstance(template_url, str) or not template_url.strip():
                raise ValueError("template_url must be a non-empty string.")
            if not isinstance(variables, dict):
                raise ValueError("variables must be a dict.")

            # Check file url
            template_url = template_url.strip()
            if not template_url.startswith(("abfss://", "file:", "dbfs:/")):
                raise ValueError("template_file must be a valid url starting with abfss://, file:, dbfs:/")

            # Read template text
            logger.debug(f"Loading template from {template_url}")
            template_html = fileutils.read_text_file(template_url)
            if not isinstance(template_html, str) or not template_html:
                raise EmailSendError("Template content is empty or not text.")

            # Render template and extract meta
            meta_recipients, meta_subject, rendered_html = self.render_email_template(
                template_html, variables
            )
            logger.debug(f"Rendered template: {rendered_html[:100]}")

            # Final subject: explicit > template meta
            # Parameterize the subject passed by function
            arg_subject = (subject or "").strip()
            if arg_subject:
                arg_subject = Template(arg_subject).safe_substitute(variables)

            # Final subject: explicit > template meta
            final_subject = arg_subject or (meta_subject or "").strip()
            if not final_subject:
                raise EmailSendError("Subject is neither provided nor found in the template meta.")
            logger.debug(f"Final subject: {final_subject}")

            # Final recipients: explicit > template meta
            if recipients is not None:
                final_recipients = self._normalize_recipients(recipients)
            else:
                if not meta_recipients:
                    raise EmailSendError(
                        "Recipients are neither provided nor found in the template meta."
                    )
                final_recipients = meta_recipients
            logger.debug(f"Final recipients: {final_recipients}")

            # Build attachments list
            attachments: List[Dict[str, str]] = []
            for name, content in [
                (attachment1_name, attachment1_content),
                (attachment2_name, attachment2_content),
            ]:
                if name and content is not None:
                    attachments.append(self.make_attachment(name, content))
                    logger.debug(f"Added attachment: {name}")

            # Validate attachments
            attachments = self._validate_attachments(attachments)
            
            # Send email
            logger.debug(f"Sending email to {final_recipients}")
            self.send_email(
                final_recipients, final_subject, rendered_html, attachments=attachments, timeout=timeout
            )

        except Exception as e:
            if isinstance(e, EmailSendError):
                raise
            raise EmailSendError(f"send_email_quick failed: {e}")
