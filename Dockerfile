# SQLcl MCP over Streamable HTTP, for Azure Container Apps.
# Build:  az acr build --registry <acr-name> --image sqlcl-mcp:1.0.0 .
#
# SQLcl is Oracle software under the Oracle Free Use license: used here unmodified, for internal use.
# Java 21 comes from the base image. SQLcl is downloaded at build time; to use a zip you already have, put it next
# to this file as sqlcl-latest.zip and build with --build-arg SQLCL_URL=local.
FROM eclipse-temurin:21-jre

ARG SQLCL_URL=https://download.oracle.com/otn_software/java/sqldeveloper/sqlcl-latest.zip

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv curl unzip ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
COPY . /tmp/ctx
RUN if [ "$SQLCL_URL" = "local" ]; then cp /tmp/ctx/sqlcl-latest.zip /opt/sqlcl.zip; \
    else curl -fsSL "$SQLCL_URL" -o /opt/sqlcl.zip; fi \
 && unzip -q /opt/sqlcl.zip -d /opt && rm /opt/sqlcl.zip \
 && test -x /opt/sqlcl/bin/sql

WORKDIR /app
COPY requirements.txt ./
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY sqlcl_http.py startup.sh ./
RUN rm -rf /tmp/ctx && chmod +x /app/startup.sh \
 && useradd --create-home --uid 10001 app && chown -R app /app
USER app

ENV SQLCL_COMMAND=/opt/sqlcl/bin/sql \
    SQLCL_MCP_HOST=0.0.0.0 \
    SQLCL_MCP_PORT=8770 \
    ORACLE_CONNECTION_NAME=vis_oracle

EXPOSE 8770
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["/opt/venv/bin/python", "-c", "import urllib.request,sys; sys.exit(0 if b'ok' in urllib.request.urlopen('http://127.0.0.1:8770/health', timeout=4).read() else 1)"]
CMD ["/app/startup.sh"]
