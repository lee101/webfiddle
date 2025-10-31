#!/usr/bin/env python3
"""
Cache Database Size Analysis
Analyzes blob field sizes in cache.db to understand data distribution
"""

import sqlite3
import time
import sys
from collections import defaultdict
import json

# Configuration
DB_PATH = "/media/lee/pcd/code/webfiddle/cache.db"
TABLE_NAME = "mirrored_content"
BATCH_SIZE = 10000

def format_bytes(bytes_val):
    """Format bytes into human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} TB"

def get_size_bucket(size_bytes):
    """Categorize file sizes into buckets"""
    if size_bytes == 0:
        return "0 B"
    elif size_bytes < 1024:
        return "< 1 KB"
    elif size_bytes < 10 * 1024:
        return "1-10 KB"
    elif size_bytes < 100 * 1024:
        return "10-100 KB"
    elif size_bytes < 1024 * 1024:
        return "100 KB - 1 MB"
    elif size_bytes < 10 * 1024 * 1024:
        return "1-10 MB"
    elif size_bytes < 100 * 1024 * 1024:
        return "10-100 MB"
    elif size_bytes < 1024 * 1024 * 1024:
        return "100 MB - 1 GB"
    else:
        return "> 1 GB"

def analyze_content_types_and_sizes():
    """Analyze content types and their size distributions"""
    print("=== Cache Database Size Analysis ===")
    print(f"Database: {DB_PATH}")
    print(f"Table: {TABLE_NAME}")
    
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60.0)
        conn.execute("PRAGMA cache_size=50000")
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB
        
        cursor = conn.cursor()
        
        # Get total count first
        print("\n📊 Getting total record count...")
        cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
        total_records = cursor.fetchone()[0]
        print(f"Total records: {total_records:,}")
        
        # Initialize counters
        size_histogram = defaultdict(int)
        content_type_sizes = defaultdict(lambda: {'count': 0, 'total_bytes': 0, 'sizes': defaultdict(int)})
        total_bytes = 0
        processed = 0
        
        print(f"\n🔍 Analyzing blob sizes in batches of {BATCH_SIZE:,}...")
        start_time = time.time()
        
        # Process in batches to handle large dataset
        offset = 0
        while offset < total_records:
            # Get batch with size info and content type
            cursor.execute(f"""
                SELECT 
                    LENGTH(data) as blob_size,
                    json_extract(headers, '$.content-type') as content_type
                FROM {TABLE_NAME} 
                ORDER BY rowid 
                LIMIT {BATCH_SIZE} OFFSET {offset}
            """)
            
            batch = cursor.fetchall()
            if not batch:
                break
            
            for blob_size, content_type in batch:
                if blob_size is None:
                    blob_size = 0
                if content_type is None:
                    content_type = "unknown"
                
                # Update size histogram
                size_bucket = get_size_bucket(blob_size)
                size_histogram[size_bucket] += 1
                
                # Update content type analysis
                content_type_clean = content_type.split(';')[0].strip().lower()
                content_type_sizes[content_type_clean]['count'] += 1
                content_type_sizes[content_type_clean]['total_bytes'] += blob_size
                content_type_sizes[content_type_clean]['sizes'][size_bucket] += 1
                
                total_bytes += blob_size
                processed += 1
            
            offset += len(batch)
            
            # Progress update
            if processed % (BATCH_SIZE * 10) == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed
                eta = (total_records - processed) / rate if rate > 0 else 0
                print(f"  Processed {processed:,}/{total_records:,} ({processed/total_records*100:.1f}%) "
                      f"- Rate: {rate:.0f} records/sec - ETA: {eta/60:.1f}min")
        
        conn.close()
        
        elapsed = time.time() - start_time
        print(f"\n✅ Analysis complete in {elapsed:.1f}s")
        print(f"📈 Processed {processed:,} records at {processed/elapsed:.0f} records/sec")
        
        # Print results
        print_analysis_results(size_histogram, content_type_sizes, total_bytes, processed)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
    
    return True

def print_analysis_results(size_histogram, content_type_sizes, total_bytes, total_records):
    """Print formatted analysis results"""
    
    print(f"\n" + "="*60)
    print("📊 SIZE DISTRIBUTION HISTOGRAM")
    print("="*60)
    
    # Sort size buckets logically
    size_order = [
        "0 B", "< 1 KB", "1-10 KB", "10-100 KB", "100 KB - 1 MB",
        "1-10 MB", "10-100 MB", "100 MB - 1 GB", "> 1 GB"
    ]
    
    print(f"{'Size Range':<20} {'Count':<12} {'Percentage':<12} {'Bar'}")
    print("-" * 60)
    
    max_count = max(size_histogram.values()) if size_histogram else 1
    for size_bucket in size_order:
        count = size_histogram.get(size_bucket, 0)
        if count > 0:
            percentage = (count / total_records) * 100
            bar_length = int((count / max_count) * 30)
            bar = "█" * bar_length
            print(f"{size_bucket:<20} {count:<12,} {percentage:<11.1f}% {bar}")
    
    print(f"\n{'Total Records:':<20} {total_records:,}")
    print(f"{'Total Storage:':<20} {format_bytes(total_bytes)}")
    print(f"{'Average Size:':<20} {format_bytes(total_bytes / total_records if total_records > 0 else 0)}")
    
    print(f"\n" + "="*60)
    print("📋 TOP CONTENT TYPES BY STORAGE")
    print("="*60)
    
    # Sort content types by total bytes
    sorted_types = sorted(content_type_sizes.items(), 
                         key=lambda x: x[1]['total_bytes'], reverse=True)
    
    print(f"{'Content Type':<35} {'Count':<10} {'Total Size':<12} {'Avg Size':<12}")
    print("-" * 75)
    
    for content_type, stats in sorted_types[:20]:  # Top 20
        count = stats['count']
        total_size = stats['total_bytes']
        avg_size = total_size / count if count > 0 else 0
        
        # Truncate long content type names
        display_type = content_type[:32] + "..." if len(content_type) > 32 else content_type
        
        print(f"{display_type:<35} {count:<10,} {format_bytes(total_size):<12} {format_bytes(avg_size):<12}")
    
    print(f"\n" + "="*60)
    print("💾 STORAGE EFFICIENCY ANALYSIS")
    print("="*60)
    
    # Calculate some interesting metrics
    small_files = size_histogram.get("< 1 KB", 0) + size_histogram.get("1-10 KB", 0)
    large_files = size_histogram.get("100 MB - 1 GB", 0) + size_histogram.get("> 1 GB", 0)
    
    print(f"Small files (< 10 KB):     {small_files:,} ({small_files/total_records*100:.1f}%)")
    print(f"Large files (> 100 MB):    {large_files:,} ({large_files/total_records*100:.1f}%)")
    
    # HTML vs other content analysis
    html_stats = content_type_sizes.get('text/html', {'count': 0, 'total_bytes': 0})
    image_stats = sum(stats['total_bytes'] for ct, stats in content_type_sizes.items() 
                     if ct.startswith('image/'))
    
    print(f"HTML content storage:      {format_bytes(html_stats['total_bytes'])}")
    print(f"Image content storage:     {format_bytes(image_stats)}")
    print(f"HTML vs Images ratio:      {html_stats['total_bytes']/image_stats:.2f}:1" if image_stats > 0 else "N/A")

def quick_stats_only():
    """Get just basic stats quickly"""
    print("=== Quick Database Stats ===")
    
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        cursor = conn.cursor()
        
        print("Getting basic statistics...")
        
        # Quick stats query
        cursor.execute(f"""
            SELECT 
                COUNT(*) as total_records,
                SUM(LENGTH(data)) as total_bytes,
                AVG(LENGTH(data)) as avg_bytes,
                MIN(LENGTH(data)) as min_bytes,
                MAX(LENGTH(data)) as max_bytes
            FROM {TABLE_NAME}
        """)
        
        stats = cursor.fetchone()
        total_records, total_bytes, avg_bytes, min_bytes, max_bytes = stats
        
        conn.close()
        
        print(f"\n📊 Quick Statistics:")
        print(f"Total Records:    {total_records:,}")
        print(f"Total Storage:    {format_bytes(total_bytes)}")
        print(f"Average Size:     {format_bytes(avg_bytes)}")
        print(f"Smallest File:    {format_bytes(min_bytes)}")
        print(f"Largest File:     {format_bytes(max_bytes)}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--quick":
        return quick_stats_only()
    else:
        return analyze_content_types_and_sizes()

if __name__ == "__main__":
    print("Cache Database Analysis Tool")
    print("Usage:")
    print("  python analyze_cache_sizes.py          # Full analysis (slower)")
    print("  python analyze_cache_sizes.py --quick  # Quick stats only")
    print()
    
    success = main()
    if not success:
        sys.exit(1) 