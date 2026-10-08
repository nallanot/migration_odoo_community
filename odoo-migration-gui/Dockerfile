FROM docker:cli

RUN apk add --no-cache python3 git
WORKDIR /app
COPY migration.py interface.html ./

EXPOSE 8765
ENTRYPOINT ["python3", "/app/migration.py"]
CMD ["--bind", "0.0.0.0", "--workdir", "/opt/odoo-migration"]
