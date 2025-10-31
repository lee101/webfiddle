#!/usr/bin/env python3
import sqlite3
import time
import sys

def delete_records_with_wal(delete_count=None):
    try:
        # Connect to the database with WAL mode for better concurrency
        conn = sqlite3.connect('cache.db', timeout=60.0)
        
        # Enable WAL mode for better concurrency
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        conn.execute('PRAGMA temp_store=memory;')
        conn.execute('PRAGMA mmap_size=268435456;')  # 256MB
        
        # Get current count
        cursor = conn.execute('SELECT COUNT(*) FROM mirrored_content')
        total_count = cursor.fetchone()[0]
        print(f'Total records in mirrored_content: {total_count}')
        
        if total_count == 0:
            print("No records to delete.")
            conn.close()
            return
        
        # Determine how many records to delete
        if delete_count is None:
            records_to_delete = 1000  # Default value
            print(f'Will delete {records_to_delete} records (default)')
        else:
            records_to_delete = min(delete_count, total_count)
            print(f'Will delete {records_to_delete} records (keeping {total_count - records_to_delete})')
        
        # Use expiry time as deletion criteria (delete oldest cached entries first)
        print("Deleting oldest cached entries based on expiry time...")
        
        # For large deletions, use batch processing
        batch_size = 5000
        total_deleted = 0
        
        while total_deleted < records_to_delete:
            remaining_to_delete = min(batch_size, records_to_delete - total_deleted)
            
            # Delete using a transaction for safety with optimized query
            conn.execute('BEGIN IMMEDIATE;')
            
            # First, find the expiry threshold for the records we want to delete
            cursor = conn.execute('''
                SELECT expiry FROM mirrored_content 
                ORDER BY expiry ASC 
                LIMIT 1 OFFSET ?
            ''', (remaining_to_delete - 1,))
            
            result = cursor.fetchone()
            if result:
                expiry_threshold = result[0]
                # Delete all records with expiry <= threshold
                cursor = conn.execute('''
                    DELETE FROM mirrored_content 
                    WHERE expiry <= ? 
                    LIMIT ?
                ''', (expiry_threshold, remaining_to_delete))
            else:
                # If we can't find a threshold, delete remaining records
                cursor = conn.execute('''
                    DELETE FROM mirrored_content 
                    WHERE key_name IN (
                        SELECT key_name FROM mirrored_content 
                        LIMIT ?
                    )
                ''', (remaining_to_delete,))
            
            batch_deleted = cursor.rowcount
            conn.commit()
            
            total_deleted += batch_deleted
            print(f'Deleted batch: {batch_deleted} records (total: {total_deleted})')
            
            # If we deleted fewer than requested, we're done
            if batch_deleted < remaining_to_delete:
                break
        
        # Verify the deletion
        cursor = conn.execute('SELECT COUNT(*) FROM mirrored_content')
        remaining_count = cursor.fetchone()[0]
        
        print(f'Successfully deleted {total_deleted} records')
        print(f'Remaining records: {remaining_count}')
        
        # Skip VACUUM for better performance - it can be run manually if needed
        # print("Optimizing database...")
        # conn.execute('VACUUM;')
        
        conn.close()
        print("Operation completed successfully!")
        
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            print("Database is still locked. The application might be very busy.")
            print("Please try stopping the application temporarily:")
            print("  sudo pkill -f 'gunicorn.*webfiddle'")
            print("Then run this script again, and restart the application afterward.")
        else:
            print(f"Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("This will delete records from the mirrored_content table.")
    print("Records will be deleted starting with the oldest (by expiry time).")
    
    response = input("Delete (h)alf, (k) records, or (c)ancel? [h/k/c]: ").lower()
    
    if response == 'h':
        # Calculate half of total records
        conn = sqlite3.connect('cache.db', timeout=60.0)
        cursor = conn.execute('SELECT COUNT(*) FROM mirrored_content')
        total_count = cursor.fetchone()[0]
        conn.close()
        half_count = total_count // 2
        print(f"Will delete {half_count} records (half of {total_count})")
        delete_records_with_wal(half_count)
    elif response == 'k':
        try:
            count = int(input("How many records to delete? [default=1000]: ") or 1000)
            delete_records_with_wal(count)
        except ValueError:
            print("Invalid number. Using default 1000 records.")
            delete_records_with_wal(1000)
    else:
        print("Operation cancelled.")