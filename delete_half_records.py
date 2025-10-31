#!/usr/bin/env python3
import sqlite3
import time
import sys

def delete_half_records():
    try:
        # Connect to the database with a timeout
        conn = sqlite3.connect('cache.db', timeout=30.0)
        
        # Get current count
        cursor = conn.execute('SELECT COUNT(*) FROM mirrored_content')
        total_count = cursor.fetchone()[0]
        print(f'Total records in mirrored_content: {total_count}')
        
        if total_count == 0:
            print("No records to delete.")
            conn.close()
            return
        
        # Calculate how many to delete (half)
        records_to_delete = total_count // 2
        print(f'Will delete {records_to_delete} records (keeping {total_count - records_to_delete})')
        
        # Confirm deletion
        response = input("Proceed with deletion? (y/N): ")
        if response.lower() != 'y':
            print("Operation cancelled.")
            conn.close()
            return
        
        # Delete half the records using ROWID (SQLite's implicit primary key)
        # This deletes the oldest records first
        cursor = conn.execute(f'''
            DELETE FROM mirrored_content 
            WHERE ROWID IN (
                SELECT ROWID FROM mirrored_content 
                ORDER BY ROWID 
                LIMIT {records_to_delete}
            )
        ''')
        
        deleted_count = cursor.rowcount
        conn.commit()
        
        # Verify the deletion
        cursor = conn.execute('SELECT COUNT(*) FROM mirrored_content')
        remaining_count = cursor.fetchone()[0]
        
        print(f'Successfully deleted {deleted_count} records')
        print(f'Remaining records: {remaining_count}')
        
        conn.close()
        
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            print("Database is locked. Please stop the webfiddle application first.")
            print("You can try again in a few moments or restart the application.")
        else:
            print(f"Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    delete_half_records() 