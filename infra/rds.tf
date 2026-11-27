# RDS Postgres 16 in the private subnets, single AZ, the smallest Graviton class.
#
# Extensions the migrations create: unaccent (001, 006), pg_trgm (006) and vector (007,
# which needs pgvector >= 0.8 for hnsw.iterative_scan). RDS for PostgreSQL ships all three;
# pgvector 0.8.0 came with minor version 16.5, and RDS's default 16.x minor is later than
# that. Migration 007 checks the installed pgvector version itself and fails loudly if it
# is too old. The master user is rds_superuser, so `martom migrate` can CREATE EXTENSION.
#
# The master password never passes through Terraform: with manage_master_user_password, RDS
# generates it and keeps it in a Secrets Manager secret that RDS owns. Terraform only knows
# the secret's ARN, so the password is in no plan, state, tfvars file or repo. ECS hands it
# to the containers as PGPASSWORD (ecs.tf); rotation is in secrets.tf.

resource "aws_db_subnet_group" "main" {
  name       = local.prefix
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier     = local.prefix
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  db_name                     = "martom"
  username                    = "martom"
  manage_master_user_password = true # RDS keeps the password in Secrets Manager (AWS-managed key)
  port                        = 5432

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  # One AZ: a Multi-AZ standby would double the database cost. An AZ outage means
  # downtime until AWS recovers it; the nightly backups cover data loss.
  multi_az = false

  backup_retention_period = var.db_backup_retention_days
  # Times are UTC. The scheduled job starts at 01:00 Sarajevo time, which is 00:00 UTC in
  # winter and 23:00 UTC in summer, and is killed after 16 h at the latest, so it is gone
  # by 17:00 UTC (job_timeout_seconds in ecs.tf; README, "Schedules"). The evening windows
  # below are clear of it all year:
  #   backup       18:00-18:30 UTC = 19:00-19:30 (winter) / 20:00-20:30 (summer) local
  #   maintenance  Wed 19:00-20:00 UTC = 20:00-21:00 / 21:00-22:00 local (may restart the DB)
  backup_window            = "18:00-18:30"
  maintenance_window       = "wed:19:00-wed:20:00"
  copy_tags_to_snapshot    = true
  delete_automated_backups = false # keep them for their retention period after a destroy

  auto_minor_version_upgrade  = true
  allow_major_version_upgrade = false

  # Postgres 15+ on RDS requires TLS by default (rds.force_ssl = 1); DATABASE_URL uses
  # sslmode=require to match.
  enabled_cloudwatch_logs_exports = ["postgresql"]
  # Create the log group (with its retention) before RDS starts writing to it.
  depends_on = [aws_cloudwatch_log_group.rds]

  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.prefix}-final"
}

# RDS would create this log group itself, with no retention (kept and billed forever, even
# after a destroy). Managing it here gives it the same retention as the container logs and
# deletes it with the stack.
resource "aws_cloudwatch_log_group" "rds" {
  name              = "/aws/rds/instance/${local.prefix}/postgresql"
  retention_in_days = var.log_retention_days
}
