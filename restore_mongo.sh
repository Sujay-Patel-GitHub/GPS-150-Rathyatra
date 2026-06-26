#!/bin/bash
# MongoDB Automated Restore Script
BACKUP_DIR="/home/lenovo1/mongo_backups"

if [ ! -d "$BACKUP_DIR" ] || [ -z "$(ls -A "$BACKUP_DIR")" ]; then
    echo "❌ ERROR: No backups found in $BACKUP_DIR"
    exit 1
fi

echo "=== MongoDB Restore Utility ==="
echo "Select a backup version to restore from:"

# List directories in backup folder
PS3="Enter the number of the backup to restore: "
select dir in "$BACKUP_DIR"/*; do
    if [ -n "$dir" ]; then
        echo "Selected backup: $dir"
        read -p "⚠️ WARNING: This will overwrite the current database. Are you sure? (y/N) " confirm
        if [[ $confirm == [yY] || $confirm == [yY][eE][sS] ]]; then
            echo "Restoring database 'gps_server_db'..."
            /usr/bin/mongorestore --db gps_server_db --drop "$dir/gps_server_db"
            if [ $? -eq 0 ]; then
                echo "✅ Database restored successfully from $dir!"
            else
                echo "❌ ERROR: Database restore failed!"
            fi
        else
            echo "Restore cancelled."
        fi
        break
    else
        echo "Invalid selection. Please try again."
    fi
done
