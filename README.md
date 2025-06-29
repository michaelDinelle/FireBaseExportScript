# Firebase Export Script

A comprehensive Python script for exporting all core data from Firebase projects, including Firestore, Authentication, Storage, and Realtime Database.

## 🚀 Features

- **Complete Firestore Export**: All collections, documents, and subcollections with proper data type preservation
- **Authentication Data**: Users, custom claims, MFA settings, and provider information
- **Storage Metadata**: File information with optional file downloads and signed URLs
- **Realtime Database**: Complete JSON data export
- **Resumable Exports**: Checkpoint system allows resuming interrupted exports
- **Safety Limits**: Built-in limits to prevent runaway costs
- **Data Integrity**: Proper serialization of Firebase-specific data types

## 📋 Prerequisites

### Required
- Python 3.7+
- Firebase Admin SDK
- Service account key with appropriate permissions

### Python Dependencies

Create a `requirements.txt` file with the following dependencies:

```txt
# Firebase Export Script Requirements
# Core Firebase Admin SDK
firebase-admin>=6.2.0
# Required dependencies for Firebase Admin SDK
google-cloud-firestore>=2.11.0
google-cloud-storage>=2.9.0
google-auth>=2.17.0
google-api-core>=2.11.0
google-cloud-core>=2.3.0
# Additional dependencies that may be needed
grpcio>=1.53.0
protobuf>=4.21.0
requests>=2.28.0
urllib3>=1.26.0
# Optional: for better error handling and logging
colorlog>=6.7.0
```

Install all dependencies:
```bash
pip install -r requirements.txt
```

Or install manually:
```bash
pip install firebase-admin google-cloud-firestore google-cloud-storage
```

### Firebase Permissions
Your service account needs these roles:
- **Firebase Admin SDK Administrator Service Agent**
- **Cloud Datastore User** (for Firestore)
- **Storage Admin** (for Cloud Storage)
- **Firebase Authentication Admin** (for Auth data)

## ⚙️ Setup

### 1. Download Service Account Key
1. Go to [Firebase Console](https://console.firebase.google.com)
2. Select your project → Project Settings → Service Accounts
3. Click "Generate New Private Key"
4. Save the JSON file securely

### 2. Configure the Script
Edit the `main()` function in the script:

```python
config = ExportConfig(
    project_id="your-project-id",
    service_account_path="path/to/service-account-key.json",
    storage_bucket="your-project-id.appspot.com",  # Optional
    realtime_db_url="https://your-project.firebaseio.com/",  # Optional
    
    # Export options
    include_subcollections=True,
    include_storage_files=False,  # Set True to download files
    max_storage_file_size_mb=100,
    
    # Safety limits
    max_firestore_reads=50000,
    max_auth_exports=50000
)
```

## 🏃‍♂️ Usage

### Basic Export
```bash
python firebase_export.py
```

The script will:
1. Verify your configuration
2. Ask for confirmation before proceeding
3. Export all configured Firebase services
4. Create a timestamped export directory

### Export Structure
```
firebase-export-YYYYMMDD_HHMMSS/
├── firestore/
│   ├── firestore_data.json           # All collections and documents
│   └── subcollections_discovered.json # Subcollection mapping
├── auth/
│   └── users.json                    # User accounts and settings
├── storage/
│   ├── files_metadata.json          # File metadata and URLs
│   └── files/                       # Downloaded files (if enabled)
├── realtime_db/
│   ├── database.json                # Complete database export
│   └── metadata.json               # Export metadata
└── export_summary.json             # Export statistics and summary
```

## 🔧 Configuration Options

### Export Options
- `include_subcollections`: Export Firestore subcollections (default: True)
- `include_storage_files`: Download actual storage files (default: False)
- `max_storage_file_size_mb`: Maximum file size to download (default: 100MB)

### Performance Settings
- `firestore_batch_size`: Documents per batch (default: 1000)
- `auth_batch_size`: Users per batch (default: 1000)
- `storage_concurrent_files`: Concurrent file downloads (default: 50)

### Safety Limits
- `max_firestore_reads`: Maximum Firestore read operations (default: 50,000)
- `max_auth_exports`: Maximum user exports (default: 50,000)

## 📊 Data Types Preserved

The script properly handles Firebase-specific data types:

- **Timestamps**: Converted with nanosecond precision
- **GeoPoints**: Latitude/longitude coordinates
- **Document References**: Full document paths
- **Binary Data**: Base64 encoded
- **Arrays and Maps**: Nested structures maintained

## 🔄 Resume Interrupted Exports

If an export is interrupted, the script automatically saves progress:

```bash
# Resume from where it left off
python firebase_export.py
```

The checkpoint system tracks:
- Completed export tasks
- Last exported document/user
- Progress through collections

## ⚠️ Important Notes

### Security
- **Keep service account keys secure** - never commit to version control
- **Review exported data** before sharing or storing
- **Generated signed URLs expire** after 7 days

### Costs
- Firestore charges for document reads
- Storage charges for download bandwidth
- Monitor usage to avoid unexpected costs

### Limitations
- Large exports may take significant time
- Storage file downloads are optional due to size/cost
- Some Firebase features may not be fully exportable

## 🐛 Troubleshooting

### Common Issues

**"Service account file not found"**
- Verify the path to your service account JSON file
- Ensure the file has proper read permissions

**"Permission denied"**
- Check that your service account has the required roles
- Verify the project ID is correct

**"Export interrupted"**
- Check available disk space
- Verify network connectivity
- Resume using the same command

### Debug Mode
Add logging configuration for more details:
```python
logging.basicConfig(level=logging.DEBUG)
```

## 📈 Performance Tips

1. **Batch Sizes**: Adjust batch sizes based on document complexity
2. **Concurrent Downloads**: Reduce concurrent files for slower connections
3. **File Downloads**: Disable for faster metadata-only exports
4. **Network**: Use stable, fast internet connection for large exports

## 🔒 Best Practices

1. **Test First**: Run on a small test project before production
2. **Regular Backups**: Schedule regular exports for data protection
3. **Secure Storage**: Encrypt exported data if storing long-term
4. **Access Control**: Limit access to exported data files
5. **Data Retention**: Follow your organization's data retention policies

## 📝 Export Summary

After completion, check `export_summary.json` for:
- Export duration and timestamps
- Document/user counts
- File sizes and statistics
- Completed tasks

## 🤝 Contributing

To improve this script:
1. Test with various Firebase configurations
2. Report issues or edge cases
3. Suggest performance improvements
4. Add support for additional Firebase services

## 📄 License

MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

**Note**: Ensure compliance with your organization's data handling policies and Firebase terms of service when using this software.
