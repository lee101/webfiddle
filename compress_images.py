#!/usr/bin/env python3
"""
Image Compression Script for Cache Database
Converts JPEG and PNG images to WebP format to reduce storage space.
Supports both static and animated images.
"""

import sqlite3
import os
import sys
import time
from pathlib import Path
from PIL import Image
import io
import json
from collections import defaultdict

# Configuration
DB_PATH = "/media/lee/pcd/code/webfiddle/cache.db"
TABLE_NAME = "mirrored_content"
WEBP_QUALITY = 85
BATCH_SIZE = 100
DRY_RUN_OUTPUT_DIR = "./webp_samples/"

# Supported image types for conversion
IMAGE_TYPES = {
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg', 
    'image/png': '.png'
}

def format_bytes(bytes_val):
    """Format bytes into human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} TB"

def get_image_records_count():
    """Get count of image records that can be compressed"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        cursor = conn.cursor()
        
        # Build WHERE clause for image types
        image_conditions = []
        for mime_type in IMAGE_TYPES.keys():
            image_conditions.append(f"json_extract(headers, '$.content-type') LIKE '{mime_type}%'")
        
        where_clause = " OR ".join(image_conditions)
        
        cursor.execute(f"""
            SELECT COUNT(*) FROM {TABLE_NAME} 
            WHERE ({where_clause}) AND LENGTH(data) > 0
        """)
        
        count = cursor.fetchone()[0]
        conn.close()
        return count
        
    except Exception as e:
        print(f"❌ Error getting image count: {e}")
        return 0

def convert_image_to_webp(image_data, original_format):
    """Convert image data to WebP format, handling both static and animated images"""
    try:
        # Load image from bytes
        image = Image.open(io.BytesIO(image_data))
        
        # Check if image is animated
        is_animated = hasattr(image, 'is_animated') and image.is_animated
        
        # Convert to WebP
        webp_buffer = io.BytesIO()
        
        if is_animated:
            # Handle animated images
            duration = image.info.get('duration', 100)
            loop = image.info.get('loop', 0)
            
            # Save all frames
            frames = []
            try:
                while True:
                    frames.append(image.copy())
                    image.seek(len(frames))  # Skip to next frame
            except EOFError:
                pass
            
            # Save as animated WebP
            if len(frames) > 1:
                frames[0].save(
                    webp_buffer,
                    format='WEBP',
                    save_all=True,
                    append_images=frames[1:],
                    duration=duration,
                    loop=loop,
                    quality=WEBP_QUALITY
                )
            else:
                # Fallback to single frame if animation fails
                image.save(webp_buffer, 'WEBP', quality=WEBP_QUALITY)
        else:
            # Handle static images
            if image.mode in ('RGBA', 'LA', 'P'):
                # For images with transparency, keep RGBA
                if image.mode == 'P' and 'transparency' in image.info:
                    image = image.convert('RGBA')
                elif image.mode == 'LA':
                    image = image.convert('RGBA')
            elif image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGB')
            
            # Use lossless for PNG with transparency, lossy for others
            if image.mode == 'RGBA' and original_format == '.png':
                image.save(webp_buffer, 'WEBP', lossless=True, quality=100)
            else:
                image.save(webp_buffer, 'WEBP', quality=WEBP_QUALITY, optimize=True)
        
        webp_data = webp_buffer.getvalue()
        webp_buffer.close()
        
        return webp_data, len(image_data), len(webp_data)
        
    except Exception as e:
        print(f"    ❌ Conversion error: {e}")
        return None, 0, 0

def update_content_type_header(headers_json, new_content_type):
    """Update content-type in headers JSON"""
    try:
        headers = json.loads(headers_json)
        headers['content-type'] = new_content_type
        
        # Update content-length if it exists
        if 'content-length' in headers:
            # This will be updated with actual size after conversion
            pass
            
        return json.dumps(headers)
    except Exception as e:
        print(f"    ⚠️  Warning: Could not update headers: {e}")
        return headers_json

def dry_run_conversion():
    """Test conversion on a few images and save samples to filesystem"""
    print("=== DRY RUN: Image Compression Test ===")
    print(f"Database: {DB_PATH}")
    print(f"Output directory: {DRY_RUN_OUTPUT_DIR}")
    print(f"WebP Quality: {WEBP_QUALITY}%")
    
    # Create output directory
    Path(DRY_RUN_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        cursor = conn.cursor()
        
        # Get sample images of each type
        samples_per_type = 3
        total_samples = 0
        total_original_size = 0
        total_webp_size = 0
        
        for mime_type, extension in IMAGE_TYPES.items():
            print(f"\n🔍 Testing {mime_type} images...")
            
            cursor.execute(f"""
                SELECT key_name, data, LENGTH(data) as size, headers
                FROM {TABLE_NAME} 
                WHERE json_extract(headers, '$.content-type') LIKE '{mime_type}%' 
                AND LENGTH(data) > 1000
                ORDER BY LENGTH(data) DESC
                LIMIT {samples_per_type}
            """)
            
            samples = cursor.fetchall()
            
            for i, (key_name, image_data, original_size, headers) in enumerate(samples):
                print(f"  Sample {i+1}: {format_bytes(original_size)}")
                
                # Convert to WebP
                webp_data, orig_size, webp_size = convert_image_to_webp(image_data, extension)
                
                if webp_data:
                    # Save original and WebP for comparison
                    original_filename = f"{mime_type.replace('/', '_')}_sample_{i+1}_original{extension}"
                    webp_filename = f"{mime_type.replace('/', '_')}_sample_{i+1}_compressed.webp"
                    
                    # Save original
                    with open(os.path.join(DRY_RUN_OUTPUT_DIR, original_filename), 'wb') as f:
                        f.write(image_data)
                    
                    # Save WebP
                    with open(os.path.join(DRY_RUN_OUTPUT_DIR, webp_filename), 'wb') as f:
                        f.write(webp_data)
                    
                    compression_ratio = (1 - webp_size / orig_size) * 100
                    print(f"    ✅ {format_bytes(orig_size)} → {format_bytes(webp_size)} "
                          f"({compression_ratio:.1f}% reduction)")
                    
                    total_original_size += orig_size
                    total_webp_size += webp_size
                    total_samples += 1
                else:
                    print(f"    ❌ Conversion failed")
        
        conn.close()
        
        if total_samples > 0:
            overall_reduction = (1 - total_webp_size / total_original_size) * 100
            print(f"\n📊 Dry Run Summary:")
            print(f"Samples tested: {total_samples}")
            print(f"Original total: {format_bytes(total_original_size)}")
            print(f"WebP total: {format_bytes(total_webp_size)}")
            print(f"Overall reduction: {overall_reduction:.1f}%")
            print(f"\nSample files saved to: {DRY_RUN_OUTPUT_DIR}")
            print("Review the samples and run without --dryrun to process the full database.")
        else:
            print("❌ No suitable images found for testing")
        
        return True
        
    except Exception as e:
        print(f"❌ Dry run error: {e}")
        return False

def compress_images_in_database():
    """Convert images in database to WebP format"""
    print("=== Image Compression: Database Update ===")
    print(f"Database: {DB_PATH}")
    print(f"WebP Quality: {WEBP_QUALITY}%")
    
    # Get total count
    total_images = get_image_records_count()
    if total_images == 0:
        print("❌ No images found to compress")
        return False
    
    print(f"Found {total_images:,} images to process")
    
    # Confirm operation
    response = input(f"\nThis will modify {total_images:,} records in the database. Continue? (y/N): ")
    if response.lower() != 'y':
        print("Operation cancelled.")
        return False
    
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60.0)
        conn.execute("PRAGMA cache_size=50000")
        conn.execute("PRAGMA journal_mode=WAL")
        
        cursor = conn.cursor()
        
        # Statistics tracking
        processed = 0
        successful_conversions = 0
        failed_conversions = 0
        total_original_bytes = 0
        total_webp_bytes = 0
        conversion_stats = defaultdict(lambda: {'count': 0, 'original_bytes': 0, 'webp_bytes': 0})
        
        start_time = time.time()
        
        # Process each image type
        for mime_type, extension in IMAGE_TYPES.items():
            print(f"\n🔄 Processing {mime_type} images...")
            
            # Get images of this type in batches
            offset = 0
            while True:
                cursor.execute(f"""
                    SELECT key_name, data, headers, LENGTH(data) as size
                    FROM {TABLE_NAME} 
                    WHERE json_extract(headers, '$.content-type') LIKE '{mime_type}%' 
                    AND LENGTH(data) > 0
                    ORDER BY key_name
                    LIMIT {BATCH_SIZE} OFFSET {offset}
                """)
                
                batch = cursor.fetchall()
                if not batch:
                    break
                
                # Process batch
                for key_name, image_data, headers_json, original_size in batch:
                    processed += 1
                    
                    if processed % 100 == 0:
                        elapsed = time.time() - start_time
                        rate = processed / elapsed
                        eta = (total_images - processed) / rate if rate > 0 else 0
                        print(f"  Progress: {processed:,}/{total_images:,} "
                              f"({processed/total_images*100:.1f}%) - "
                              f"Rate: {rate:.1f}/sec - ETA: {eta/60:.1f}min")
                    
                    # Convert image
                    webp_data, orig_size, webp_size = convert_image_to_webp(image_data, extension)
                    
                    if webp_data and webp_size > 0:
                        # Update headers
                        updated_headers = update_content_type_header(headers_json, 'image/webp')
                        
                        # Update headers with new content-length
                        try:
                            headers_dict = json.loads(updated_headers)
                            headers_dict['content-length'] = str(webp_size)
                            updated_headers = json.dumps(headers_dict)
                        except:
                            pass  # Keep original headers if JSON parsing fails
                        
                        # Update database record
                        try:
                            cursor.execute(f"""
                                UPDATE {TABLE_NAME} 
                                SET data = ?, headers = ?
                                WHERE key_name = ?
                            """, (webp_data, updated_headers, key_name))
                            
                            successful_conversions += 1
                            total_original_bytes += orig_size
                            total_webp_bytes += webp_size
                            
                            # Track stats by type
                            conversion_stats[mime_type]['count'] += 1
                            conversion_stats[mime_type]['original_bytes'] += orig_size
                            conversion_stats[mime_type]['webp_bytes'] += webp_size
                            
                        except Exception as e:
                            print(f"    ❌ Database update failed for {key_name}: {e}")
                            failed_conversions += 1
                    else:
                        failed_conversions += 1
                
                offset += len(batch)
                
                # Commit batch
                conn.commit()
        
        conn.close()
        
        # Print final statistics
        elapsed = time.time() - start_time
        print(f"\n" + "="*60)
        print("✅ COMPRESSION COMPLETE")
        print("="*60)
        print(f"Total processed: {processed:,}")
        print(f"Successful conversions: {successful_conversions:,}")
        print(f"Failed conversions: {failed_conversions:,}")
        print(f"Processing time: {elapsed:.1f}s ({processed/elapsed:.1f} images/sec)")
        
        if total_original_bytes > 0:
            overall_reduction = (1 - total_webp_bytes / total_original_bytes) * 100
            space_saved = total_original_bytes - total_webp_bytes
            
            print(f"\n💾 Storage Impact:")
            print(f"Original size: {format_bytes(total_original_bytes)}")
            print(f"WebP size: {format_bytes(total_webp_bytes)}")
            print(f"Space saved: {format_bytes(space_saved)} ({overall_reduction:.1f}% reduction)")
            
            print(f"\n📊 Breakdown by Type:")
            for mime_type, stats in conversion_stats.items():
                if stats['count'] > 0:
                    type_reduction = (1 - stats['webp_bytes'] / stats['original_bytes']) * 100
                    print(f"{mime_type:<15}: {stats['count']:,} files, "
                          f"{format_bytes(stats['original_bytes'])} → {format_bytes(stats['webp_bytes'])} "
                          f"({type_reduction:.1f}% reduction)")
        
        return True
        
    except Exception as e:
        print(f"❌ Compression error: {e}")
        return False

def main():
    print("Image Compression Tool for Cache Database")
    print("=" * 60)
    
    # Check if PIL is available
    try:
        from PIL import Image
    except ImportError:
        print("❌ Error: Pillow library not found!")
        print("Install with: pip install Pillow")
        return False
    
    # Check database exists
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found: {DB_PATH}")
        return False
    
    # Get image count
    image_count = get_image_records_count()
    print(f"Found {image_count:,} images that can be compressed")
    
    if image_count == 0:
        print("No images found to process.")
        return True
    
    # Check for dry run
    if len(sys.argv) > 1 and sys.argv[1] == "--dryrun":
        return dry_run_conversion()
    else:
        return compress_images_in_database()

if __name__ == "__main__":
    print("Usage:")
    print("  python compress_images.py --dryrun    # Test conversion on samples")
    print("  python compress_images.py             # Compress all images in database")
    print()
    
    success = main()
    if not success:
        sys.exit(1) 