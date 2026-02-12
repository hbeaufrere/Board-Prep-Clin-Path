/**
 * Google Apps Script - Drive File API for Vet Clin Path MCQ Generator
 *
 * SETUP INSTRUCTIONS:
 * 1. Go to https://script.google.com/
 * 2. Click "New project"
 * 3. Delete the default code and paste this entire file
 * 4. Replace FOLDER_ID below with your Google Drive folder ID
 *    (The folder ID is the last part of the folder URL:
 *     https://drive.google.com/drive/folders/YOUR_FOLDER_ID_HERE)
 * 5. Click "Deploy" > "New deployment"
 * 6. Choose "Web app" as the type
 * 7. Set "Execute as" to "Me"
 * 8. Set "Who has access" to "Anyone"
 * 9. Click "Deploy" and authorize when prompted
 * 10. Copy the Web app URL and set it as GOOGLE_APPS_SCRIPT_URL in your .env file
 */

// CHANGE THIS to your Google Drive folder ID
const FOLDER_ID = 'YOUR_FOLDER_ID_HERE';

function doGet(e) {
  var action = e.parameter.action || 'list';
  var result;

  try {
    if (action === 'list') {
      result = listFiles();
    } else if (action === 'content') {
      var fileId = e.parameter.fileId;
      if (!fileId) {
        result = { success: false, error: 'Missing fileId parameter' };
      } else {
        result = getFileContent(fileId);
      }
    } else {
      result = { success: false, error: 'Unknown action: ' + action };
    }
  } catch (err) {
    result = { success: false, error: err.toString() };
  }

  return ContentService
    .createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}

function listFiles() {
  var folder = DriveApp.getFolderById(FOLDER_ID);
  var files = folder.getFiles();
  var fileList = [];

  while (files.hasNext()) {
    var file = files.next();
    var mimeType = file.getMimeType();

    // Only include supported file types
    if (mimeType === 'application/pdf' ||
        mimeType === 'application/vnd.google-apps.document' ||
        mimeType === 'text/plain' ||
        mimeType === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') {
      fileList.push({
        id: file.getId(),
        name: file.getName(),
        mimeType: mimeType,
        size: file.getSize(),
        lastUpdated: file.getLastUpdated().toISOString(),
        url: file.getUrl()
      });
    }
  }

  // Sort by name
  fileList.sort(function(a, b) {
    return a.name.localeCompare(b.name);
  });

  return { success: true, files: fileList, count: fileList.length };
}

function getFileContent(fileId) {
  var file = DriveApp.getFileById(fileId);
  var mimeType = file.getMimeType();
  var text = '';

  if (mimeType === 'application/vnd.google-apps.document') {
    // Google Doc - extract text directly
    var doc = DocumentApp.openById(fileId);
    text = doc.getBody().getText();

  } else if (mimeType === 'text/plain') {
    // Plain text file
    text = file.getBlob().getDataAsString();

  } else if (mimeType === 'application/pdf') {
    // PDF - use Drive's OCR conversion to extract text
    var blob = file.getBlob();
    var resource = {
      title: file.getName().replace('.pdf', ''),
      mimeType: 'application/vnd.google-apps.document'
    };
    var options = {
      ocr: true,
      ocrLanguage: 'en'
    };
    var tempDoc = Drive.Files.insert(resource, blob, options);
    var doc = DocumentApp.openById(tempDoc.id);
    text = doc.getBody().getText();
    // Clean up the temporary file
    DriveApp.getFileById(tempDoc.id).setTrashed(true);

  } else if (mimeType === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') {
    // Word doc - convert via Google Docs
    var blob = file.getBlob();
    var resource = {
      title: file.getName().replace('.docx', ''),
      mimeType: 'application/vnd.google-apps.document'
    };
    var tempDoc = Drive.Files.insert(resource, blob, { convert: true });
    var doc = DocumentApp.openById(tempDoc.id);
    text = doc.getBody().getText();
    DriveApp.getFileById(tempDoc.id).setTrashed(true);

  } else {
    return { success: false, error: 'Unsupported file type: ' + mimeType };
  }

  // Truncate very long texts (Apps Script has response size limits)
  var maxLength = 50000;
  if (text.length > maxLength) {
    text = text.substring(0, maxLength) + '\n\n[Text truncated at ' + maxLength + ' characters]';
  }

  return {
    success: true,
    fileId: fileId,
    fileName: file.getName(),
    mimeType: mimeType,
    content: text,
    contentLength: text.length
  };
}
