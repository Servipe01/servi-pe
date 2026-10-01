/**
 * Servi.pe bot bridge. Paste this into the PUBLIC profiles sheet:
 *   Extensions > Apps Script, replace everything, save.
 * Then in Project Settings > Script properties add:
 *   SECRET            (the secret printed by the server setup script)
 *   PRIVATE_SHEET_ID  (ID of the private "Servi Privado" sheet)
 * Deploy > New deployment > Web app, Execute as: Me, Who has access: Anyone.
 * Copy the Web app URL into the server config (SHEETS_URL).
 */

var PRIVATE_HEADERS = ['ID', 'Fecha', 'Nombre', 'Apellido', 'DNI', 'WhatsApp origen',
  'WhatsApp contacto', 'Foto DNI (archivo en servidor)', 'Estado'];

function norm_(s) {
  return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function publicSheet_() {
  var ws = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  var headers = ws.getRange(1, 1, 1, ws.getLastColumn()).getValues()[0];
  if (headers.map(norm_).indexOf('id') === -1) {
    ws.getRange(1, headers.length + 1).setValue('ID');
  }
  return ws;
}

function privateSheet_() {
  var id = PropertiesService.getScriptProperties().getProperty('PRIVATE_SHEET_ID');
  if (!id) return null;
  var ws = SpreadsheetApp.openById(id).getSheets()[0];
  if (ws.getLastRow() === 0) ws.appendRow(PRIVATE_HEADERS);
  return ws;
}

function headers_(ws) {
  return ws.getRange(1, 1, 1, ws.getLastColumn()).getValues()[0].map(norm_);
}

function append_(ws, values) {
  var heads = headers_(ws);
  var lookup = {};
  Object.keys(values).forEach(function (k) { lookup[norm_(k)] = values[k]; });
  var row = heads.map(function (h) { return lookup.hasOwnProperty(h) ? lookup[h] : ''; });
  // write as plain text so phone numbers and DNIs keep their digits
  var r = ws.getLastRow() + 1;
  ws.getRange(r, 1, 1, row.length).setNumberFormat('@').setValues([row]);
}

function setById_(ws, id, updates) {
  var heads = headers_(ws);
  var col = heads.indexOf('id') + 1;
  if (col === 0 || ws.getLastRow() < 2) return false;
  var ids = ws.getRange(2, col, ws.getLastRow() - 1, 1).getValues();
  for (var i = 0; i < ids.length; i++) {
    if (String(ids[i][0]).trim() === String(id)) {
      Object.keys(updates).forEach(function (k) {
        var c = heads.indexOf(norm_(k)) + 1;
        if (c > 0) ws.getRange(i + 2, c).setValue(updates[k]);
      });
      return true;
    }
  }
  return false;
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var req = JSON.parse(e.postData.contents);
    var secret = PropertiesService.getScriptProperties().getProperty('SECRET');
    if (!secret || req.secret !== secret) return json_({ ok: false, error: 'unauthorized' });

    var pub = publicSheet_();
    var priv = privateSheet_();
    if (req.action === 'append') {
      append_(pub, req.public);
      if (priv) append_(priv, req.private);
      return json_({ ok: true });
    }
    if (req.action === 'status') {
      var found = setById_(pub, req.id, req.updates || {});
      if (priv) setById_(priv, req.id, req.private_updates || {});
      return json_({ ok: found });
    }
    return json_({ ok: false, error: 'unknown action' });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}
