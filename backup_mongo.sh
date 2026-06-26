#!/bin/bash
# MongoDB Automated Backup Script
BACKUP_DIR="/home/lenovo1/mongo_backups"
TIMESTAMP=$(date +%F_%H-%M-%S)
DEST="$BACKUP_DIR/$TIMESTAMP"

mkdir -p "$BACKUP_DIR"

echo "=== Starting MongoDB Backup: $(date) ==="
echo "Dumping database 'gps_server_db' to $DEST..."

# Run mongodump
/usr/bin/mongodump --db gps_server_db --out "$DEST"

if [ $? -eq 0 ]; then
    echo "✅ Backup completed successfully!"
    
    # Clean up backups older than 7 days (10080 minutes)
    echo "Cleaning up backups older than 7 days..."
    find "$BACKUP_DIR" -maxdepth 1 -type d -mmin +10080 -exec rm -rf {} \;
    echo "✅ Cleanup completed."
else
    echo "❌ ERROR: MongoDB backup failed!"
    exit 1
fi
