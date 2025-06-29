#!/usr/bin/env python3
"""
Streamlined Firebase Export Script - Core Data Only
Exports only the essential data:
- Firestore (all collections + subcollections)
- Authentication (users + custom claims + MFA + provider data)
- Storage (complete metadata + optional file downloads)
- Realtime Database (complete JSON tree)
"""

import firebase_admin
from firebase_admin import credentials, firestore, auth, storage, db
import json
import os
import sys
import time
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import base64

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class ExportConfig:
    """Configuration for the export process"""
    project_id: str
    service_account_path: str
    storage_bucket: Optional[str] = None
    realtime_db_url: Optional[str] = None
    
    # Export options
    include_subcollections: bool = True
    include_storage_files: bool = False  # Set True to download actual files
    max_storage_file_size_mb: int = 100
    
    # Rate limiting
    firestore_batch_size: int = 1000
    auth_batch_size: int = 1000
    storage_concurrent_files: int = 50
    
    # Safety limits
    max_firestore_reads: int = 50000
    max_auth_exports: int = 50000

@dataclass
class ExportStats:
    """Track export statistics"""
    firestore_reads: int = 0
    firestore_collections: int = 0
    firestore_subcollections: int = 0
    auth_users: int = 0
    storage_files: int = 0
    storage_bytes: int = 0
    start_time: datetime = field(default_factory=datetime.now)
    
    def print_summary(self):
        duration = datetime.now() - self.start_time
        logger.info(f"""
📊 EXPORT SUMMARY:
├── Duration: {duration}
├── Firestore: {self.firestore_collections} collections, {self.firestore_subcollections} subcollections, {self.firestore_reads} reads
├── Authentication: {self.auth_users} users
├── Storage: {self.storage_files} files, {self.storage_bytes / (1024*1024):.1f}MB
└── Realtime DB: Complete export
        """)

class StreamlinedFirebaseExporter:
    def __init__(self, config: ExportConfig):
        self.config = config
        self.stats = ExportStats()
        self.export_dir = f"firebase-export-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.checkpoint_file = os.path.join(self.export_dir, ".checkpoint.json")
        
        # Initialize Firebase
        self._initialize_firebase()
        
        # Create export directories
        os.makedirs(self.export_dir, exist_ok=True)
        for subdir in ['firestore', 'auth', 'storage', 'realtime_db']:
            os.makedirs(os.path.join(self.export_dir, subdir), exist_ok=True)
        
        # Load checkpoint
        self.checkpoint = self._load_checkpoint()
        
        # Track discovered subcollections
        self.discovered_subcollections: Set[str] = set()
    
    def _initialize_firebase(self):
        """Initialize Firebase Admin SDK"""
        try:
            cred = credentials.Certificate(self.config.service_account_path)
            app_config = {'projectId': self.config.project_id}
            
            if self.config.storage_bucket:
                app_config['storageBucket'] = self.config.storage_bucket
            
            if self.config.realtime_db_url:
                app_config['databaseURL'] = self.config.realtime_db_url
            
            firebase_admin.initialize_app(cred, app_config)
            
            self.firestore_client = firestore.client()
            self.storage_bucket = storage.bucket() if self.config.storage_bucket else None
            self.realtime_db = db.reference() if self.config.realtime_db_url else None
            
            logger.info("✅ Firebase Admin SDK initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Firebase: {e}")
            sys.exit(1)
    
    def _load_checkpoint(self) -> Dict:
        """Load export checkpoint for resumability"""
        if os.path.exists(self.checkpoint_file):
            with open(self.checkpoint_file, 'r') as f:
                return json.load(f)
        return {
            "completed_tasks": [],
            "firestore_collections": {},
            "auth_last_uid": None,
            "storage_files": []
        }
    
    def _save_checkpoint(self):
        """Save current progress"""
        with open(self.checkpoint_file, 'w') as f:
            json.dump(self.checkpoint, f, indent=2)
    
    def _check_limits(self):
        """Check if we're approaching safety limits"""
        if self.stats.firestore_reads >= self.config.max_firestore_reads:
            logger.error(f"🛑 Firestore read limit reached: {self.stats.firestore_reads}")
            sys.exit(1)
        
        if self.stats.auth_users >= self.config.max_auth_exports:
            logger.error(f"🛑 Auth export limit reached: {self.stats.auth_users}")
            sys.exit(1)
    
    def _serialize_firestore_value(self, value: Any) -> Any:
        """Convert Firestore-specific types to JSON-serializable format"""
        if isinstance(value, dict):
            return {k: self._serialize_firestore_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._serialize_firestore_value(item) for item in value]
        elif hasattr(value, 'timestamp') and hasattr(value, 'nanosecond'):
            # DatetimeWithNanoseconds
            return {
                "_type": "timestamp",
                "value": value.isoformat(),
                "nanoseconds": getattr(value, 'nanosecond', 0)
            }
        elif str(type(value)) == "<class 'google.cloud.firestore_v1._helpers.DatetimeWithNanoseconds'>":
            return {
                "_type": "timestamp", 
                "value": value.isoformat(),
                "nanoseconds": getattr(value, 'nanosecond', 0)
            }
        elif hasattr(value, 'latitude') and hasattr(value, 'longitude'):
            # GeoPoint
            return {
                "_type": "geopoint",
                "latitude": value.latitude,
                "longitude": value.longitude
            }
        elif hasattr(value, 'path'):
            # DocumentReference
            return {
                "_type": "reference",
                "path": value.path
            }
        elif isinstance(value, bytes):
            # Binary data
            return {
                "_type": "bytes",
                "value": base64.b64encode(value).decode('utf-8')
            }
        elif isinstance(value, datetime):
            return {
                "_type": "datetime",
                "value": value.isoformat()
            }
        else:
            try:
                json.dumps(value)
                return value
            except (TypeError, ValueError):
                return {
                    "_type": "serialized",
                    "value": str(value)
                }
    
    def _discover_subcollections(self, doc_ref) -> List[str]:
        """Discover all subcollections for a document"""
        try:
            subcollections = []
            for subcol in doc_ref.collections():
                # Use the full path from the document reference
                subcol_path = f"{doc_ref.path}/{subcol.id}"
                subcollections.append(subcol_path)
                self.discovered_subcollections.add(subcol_path)
            return subcollections
        except Exception as e:
            logger.debug(f"Could not discover subcollections for {doc_ref.path}: {e}")
            return []
    
    async def export_firestore(self):
        """Export all Firestore data including subcollections"""
        if "firestore" in self.checkpoint["completed_tasks"]:
            logger.info("⏭️  Skipping Firestore (already completed)")
            return
        
        logger.info("🔥 Starting Firestore export...")
        
        # Get all root collections
        collections = list(self.firestore_client.collections())
        logger.info(f"Found {len(collections)} root collections")
        
        all_data = {}
        
        for collection in collections:
            collection_id = collection.id
            
            if collection_id in self.checkpoint["firestore_collections"]:
                logger.info(f"⏭️  Skipping collection {collection_id} (already exported)")
                continue
            
            logger.info(f"📥 Exporting collection: {collection_id}")
            
            # Export main collection
            docs = await self._export_collection(collection)
            all_data[collection_id] = docs
            
            # Export subcollections if enabled
            if self.config.include_subcollections:
                subcollection_data = await self._export_subcollections(collection)
                if subcollection_data:
                    all_data[f"{collection_id}_subcollections"] = subcollection_data
            
            self.stats.firestore_collections += 1
            self.checkpoint["firestore_collections"][collection_id] = True
            self._save_checkpoint()
            self._check_limits()
        
        # Save main data
        output_file = os.path.join(self.export_dir, 'firestore', 'firestore_data.json')
        with open(output_file, 'w') as f:
            json.dump(all_data, f, indent=2, default=str)
        
        # Save discovered subcollections map
        subcol_file = os.path.join(self.export_dir, 'firestore', 'subcollections_discovered.json')
        with open(subcol_file, 'w') as f:
            json.dump(list(self.discovered_subcollections), f, indent=2)
        
        self.checkpoint["completed_tasks"].append("firestore")
        self._save_checkpoint()
        logger.info(f"✅ Firestore export complete: {len(all_data)} collections")
    
    async def _export_collection(self, collection_ref) -> List[Dict]:
        """Export a single collection"""
        documents = []
        doc_count = 0
        last_doc = None
        
        while True:
            try:
                query = collection_ref.limit(self.config.firestore_batch_size)
                if last_doc:
                    query = query.start_after(last_doc)
                
                docs = query.get()
                self.stats.firestore_reads += len(docs)
                
                if not docs:
                    break
                
                for doc in docs:
                    # Export document data
                    doc_data = doc.to_dict()
                    doc_data = self._serialize_firestore_value(doc_data)
                    
                    # Create document export
                    doc_export = {
                        "_id": doc.id,
                        "_path": doc.reference.path,
                        "_data": doc_data
                    }
                    
                    # Add metadata
                    if doc.create_time:
                        doc_export["_create_time"] = doc.create_time.isoformat()
                    if doc.update_time:
                        doc_export["_update_time"] = doc.update_time.isoformat()
                    
                    # Discover subcollections
                    if self.config.include_subcollections:
                        subcollections = self._discover_subcollections(doc.reference)
                        if subcollections:
                            doc_export["_subcollections"] = subcollections
                    
                    documents.append(doc_export)
                    doc_count += 1
                
                last_doc = docs[-1]
                
                if doc_count % 100 == 0:
                    logger.info(f"  📄 Exported {doc_count} documents from {collection_ref.id}")
                
                # Small delay to be nice to Firebase
                await asyncio.sleep(0.01)
                
            except Exception as e:
                logger.error(f"Error exporting collection {collection_ref.id}: {e}")
                break
        
        return documents
    
    async def _export_subcollections(self, parent_collection) -> Dict:
        """Export all subcollections recursively"""
        subcollection_data = {}
        
        # Build parent collection path - it's just the collection ID for root collections
        parent_collection_id = parent_collection.id
        
        for subcol_path in list(self.discovered_subcollections):
            try:
                # Check if this subcollection belongs to documents in the current collection
                # Subcollection paths look like: "users/user123/profile" where "users" is parent collection
                path_parts = subcol_path.split('/')
                if len(path_parts) >= 3 and path_parts[0] == parent_collection_id:
                    
                    logger.info(f"  🔗 Exporting subcollection: {subcol_path}")
                    
                    # Navigate to subcollection
                    ref = self.firestore_client
                    
                    for i in range(0, len(path_parts), 2):
                        if i + 1 < len(path_parts):
                            # Document path
                            ref = ref.collection(path_parts[i]).document(path_parts[i + 1])
                        else:
                            # Collection path
                            ref = ref.collection(path_parts[i])
                    
                    docs = await self._export_collection(ref)
                    subcollection_data[subcol_path] = docs
                    
                    self.stats.firestore_subcollections += 1
                    
            except Exception as e:
                logger.warning(f"Could not export subcollection {subcol_path}: {e}")
        
        return subcollection_data
    
    async def export_auth(self):
        """Export all Firebase Auth data"""
        if "auth" in self.checkpoint["completed_tasks"]:
            logger.info("⏭️  Skipping Auth (already completed)")
            return
        
        logger.info("👤 Starting Auth export...")
        
        users = []
        page_token = None
        
        while True:
            try:
                result = auth.list_users(page_token=page_token, max_results=self.config.auth_batch_size)
                
                for user in result.users:
                    # Skip if we've already exported this user
                    if (self.checkpoint["auth_last_uid"] and 
                        user.uid <= self.checkpoint["auth_last_uid"]):
                        continue
                    
                    # Export comprehensive user data
                    user_data = {
                        "uid": user.uid,
                        "email": user.email,
                        "email_verified": user.email_verified,
                        "display_name": user.display_name,
                        "photo_url": user.photo_url,
                        "phone_number": user.phone_number,
                        "disabled": user.disabled,
                        "creation_timestamp": user.user_metadata.creation_timestamp,
                        "last_sign_in_timestamp": user.user_metadata.last_sign_in_timestamp,
                        "custom_claims": user.custom_claims or {},
                        "provider_data": []
                    }
                    
                    # Export provider data
                    if user.provider_data:
                        for provider in user.provider_data:
                            user_data["provider_data"].append({
                                "provider_id": provider.provider_id,
                                "uid": provider.uid,
                                "email": provider.email,
                                "display_name": provider.display_name,
                                "photo_url": provider.photo_url
                            })
                    
                    # Try to get MFA enrollment data
                    try:
                        user_record = auth.get_user(user.uid)
                        if hasattr(user_record, 'multi_factor') and user_record.multi_factor:
                            mfa_info = user_record.multi_factor
                            if mfa_info.enrolled_factors:
                                user_data["mfa_enrolled_factors"] = [
                                    {
                                        "uid": factor.uid,
                                        "display_name": factor.display_name,
                                        "factor_id": factor.factor_id,
                                        "enrollment_time": factor.enrollment_time.isoformat() if factor.enrollment_time else None
                                    }
                                    for factor in mfa_info.enrolled_factors
                                ]
                    except Exception as e:
                        logger.debug(f"Could not get MFA data for user {user.uid}: {e}")
                    
                    users.append(user_data)
                    self.stats.auth_users += 1
                    self.checkpoint["auth_last_uid"] = user.uid
                    
                    if len(users) % 100 == 0:
                        logger.info(f"  👥 Exported {len(users)} users")
                        self._save_checkpoint()
                        self._check_limits()
                
                page_token = result.next_page_token
                if not page_token:
                    break
                    
            except Exception as e:
                logger.error(f"Error exporting auth data: {e}")
                break
        
        # Save auth data
        auth_file = os.path.join(self.export_dir, 'auth', 'users.json')
        with open(auth_file, 'w') as f:
            json.dump(users, f, indent=2)
        
        self.checkpoint["completed_tasks"].append("auth")
        self._save_checkpoint()
        logger.info(f"✅ Auth export complete: {len(users)} users")
    
    async def export_storage(self):
        """Export Storage metadata and optionally files"""
        if "storage" in self.checkpoint["completed_tasks"]:
            logger.info("⏭️  Skipping Storage (already completed)")
            return
        
        if not self.storage_bucket:
            logger.info("⏭️  Skipping Storage (no bucket configured)")
            return
        
        logger.info("📦 Starting Storage export...")
        
        files_metadata = []
        
        try:
            blobs = self.storage_bucket.list_blobs()
            
            with ThreadPoolExecutor(max_workers=self.config.storage_concurrent_files) as executor:
                future_to_blob = {}
                
                for blob in blobs:
                    if blob.name in self.checkpoint["storage_files"]:
                        continue
                    
                    future = executor.submit(self._process_storage_file, blob)
                    future_to_blob[future] = blob
                
                for future in as_completed(future_to_blob):
                    blob = future_to_blob[future]
                    try:
                        file_data = future.result()
                        if file_data:
                            files_metadata.append(file_data)
                            
                            self.stats.storage_files += 1
                            self.stats.storage_bytes += file_data.get("size", 0)
                            
                            self.checkpoint["storage_files"].append(blob.name)
                            
                            if len(files_metadata) % 50 == 0:
                                logger.info(f"  📁 Processed {len(files_metadata)} files")
                                self._save_checkpoint()
                    
                    except Exception as e:
                        logger.error(f"Error processing file {blob.name}: {e}")
        
        except Exception as e:
            logger.error(f"Error listing storage files: {e}")
        
        # Save metadata
        metadata_file = os.path.join(self.export_dir, 'storage', 'files_metadata.json')
        with open(metadata_file, 'w') as f:
            json.dump(files_metadata, f, indent=2)
        
        self.checkpoint["completed_tasks"].append("storage")
        self._save_checkpoint()
        logger.info(f"✅ Storage export complete: {len(files_metadata)} files")
    
    def _process_storage_file(self, blob) -> Optional[Dict]:
        """Process a single storage file"""
        try:
            file_data = {
                "name": blob.name,
                "bucket": blob.bucket.name,
                "size": blob.size,
                "content_type": blob.content_type,
                "time_created": blob.time_created.isoformat() if blob.time_created else None,
                "updated": blob.updated.isoformat() if blob.updated else None,
                "etag": blob.etag,
                "md5_hash": blob.md5_hash,
                "crc32c": blob.crc32c,
                "metadata": blob.metadata or {},
                "cache_control": blob.cache_control,
                "content_disposition": blob.content_disposition,
                "content_encoding": blob.content_encoding,
                "content_language": blob.content_language
            }
            
            # Generate download URL
            try:
                file_data["download_url"] = blob.generate_signed_url(
                    expiration=datetime.now(timezone.utc).replace(tzinfo=None) + 
                    timedelta(days=7)
                )
            except Exception as e:
                logger.debug(f"Could not generate signed URL for {blob.name}: {e}")
            
            # Optionally download small files
            if (self.config.include_storage_files and 
                blob.size and 
                blob.size < self.config.max_storage_file_size_mb * 1024 * 1024):
                
                try:
                    file_dir = os.path.join(self.export_dir, 'storage', 'files')
                    os.makedirs(file_dir, exist_ok=True)
                    
                    # Clean filename for local storage
                    safe_filename = blob.name.replace('/', '_').replace('\\', '_')
                    file_path = os.path.join(file_dir, safe_filename)
                    
                    blob.download_to_filename(file_path)
                    file_data["local_path"] = file_path
                    
                    # Calculate checksum
                    with open(file_path, 'rb') as f:
                        file_data["sha256_checksum"] = hashlib.sha256(f.read()).hexdigest()
                        
                except Exception as e:
                    logger.warning(f"Could not download file {blob.name}: {e}")
            
            return file_data
            
        except Exception as e:
            logger.error(f"Error processing storage file {blob.name}: {e}")
            return None
    
    async def export_realtime_database(self):
        """Export Realtime Database data"""
        if "realtime_db" in self.checkpoint["completed_tasks"]:
            logger.info("⏭️  Skipping Realtime Database (already completed)")
            return
        
        if not self.realtime_db:
            logger.info("⏭️  Skipping Realtime Database (not configured)")
            return
        
        logger.info("🔄 Starting Realtime Database export...")
        
        try:
            # Export entire database
            data = self.realtime_db.get()
            
            # Save data
            db_file = os.path.join(self.export_dir, 'realtime_db', 'database.json')
            with open(db_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            
            # Save metadata
            metadata = {
                "export_time": datetime.now().isoformat(),
                "database_url": self.config.realtime_db_url,
                "data_type": type(data).__name__,
                "estimated_size_bytes": len(json.dumps(data, default=str)) if data else 0
            }
            
            metadata_file = os.path.join(self.export_dir, 'realtime_db', 'metadata.json')
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            self.checkpoint["completed_tasks"].append("realtime_db")
            self._save_checkpoint()
            logger.info("✅ Realtime Database export complete")
            
        except Exception as e:
            logger.error(f"Error exporting Realtime Database: {e}")
    
    def _create_export_summary(self):
        """Create export summary"""
        summary = {
            "export_metadata": {
                "export_time": self.stats.start_time.isoformat(),
                "completion_time": datetime.now().isoformat(),
                "duration_seconds": (datetime.now() - self.stats.start_time).total_seconds()
            },
            "statistics": {
                "firestore": {
                    "collections": self.stats.firestore_collections,
                    "subcollections": self.stats.firestore_subcollections,
                    "reads": self.stats.firestore_reads
                },
                "auth": {
                    "users": self.stats.auth_users
                },
                "storage": {
                    "files": self.stats.storage_files,
                    "bytes": self.stats.storage_bytes
                }
            },
            "completed_tasks": self.checkpoint["completed_tasks"]
        }
        
        summary_file = os.path.join(self.export_dir, 'export_summary.json')
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        
        return summary
    
    async def run_export(self):
        """Run the complete Firebase export"""
        logger.info("🚀 Starting Firebase Core Data Export")
        logger.info("=" * 50)
        
        try:
            # Run core exports
            await self.export_firestore()
            await self.export_auth()
            await self.export_storage()
            await self.export_realtime_database()
            
            # Create summary
            self._create_export_summary()
            
            # Final summary
            self.stats.print_summary()
            
            # Clean up checkpoint on success
            if os.path.exists(self.checkpoint_file):
                os.remove(self.checkpoint_file)
            
            logger.info("🎉 FIREBASE EXPORT COMPLETE!")
            logger.info(f"📁 Export location: {self.export_dir}")
            
        except KeyboardInterrupt:
            logger.warning("\n⚠️  Export interrupted by user")
            self.stats.print_summary()
            logger.info("💾 Progress saved - you can resume later")
        except Exception as e:
            logger.error(f"\n❌ Export failed: {e}")
            self.stats.print_summary()
            logger.info("💾 Progress saved - you can resume later")
            raise

def main():
    print("🔥 STREAMLINED FIREBASE EXPORT")
    print("=" * 40)
    print("Exports core Firebase data:")
    print("✅ Firestore (all collections + subcollections)")
    print("✅ Authentication (users + custom claims + MFA)")
    print("✅ Storage (metadata + optional files)")
    print("✅ Realtime Database (complete data)")
    print("=" * 40)
    
    # Configuration
    config = ExportConfig(
        project_id="",
        service_account_path="",
        storage_bucket="",  # Update if different
        realtime_db_url=None,  # Add if you use Realtime Database: "https://your-project.firebaseio.com/"
        
        # Export options
        include_subcollections=True,
        include_storage_files=False,  # Set to True to download actual files
        max_storage_file_size_mb=100,
        
        # Safety limits
        max_firestore_reads=50000,
        max_auth_exports=50000
    )
    
    # Verify service account file
    if not os.path.exists(config.service_account_path):
        print(f"❌ Service account file not found: {config.service_account_path}")
        print("\nDownload your Firebase service account key:")
        print("1. Firebase Console → Project Settings → Service Accounts")
        print("2. Click 'Generate New Private Key'")
        print("3. Save the file in this directory")
        sys.exit(1)
    
    # Confirm export
    print(f"\n🔥 About to export from project: {config.project_id}")
    print("This will export ALL your core Firebase data.")
    
    response = input("\nProceed with export? (yes/no): ")
    if response.lower() != 'yes':
        print("Export cancelled.")
        sys.exit(0)
    
    # Run export
    exporter = StreamlinedFirebaseExporter(config)
    asyncio.run(exporter.run_export())

if __name__ == "__main__":
    main()
