/**
 * google_apps_script.js
 * ---------------------
 * Script recebedor para o Google Drive (em https://script.google.com).
 *
 * Funcionalidades:
 * 1. `action: "read"` -> Lê o conteúdo de um arquivo (usado pela Vercel para carregar o `_index.json`).
 * 2. `overwrite: true` -> Atualiza arquivos no mesmo lugar (sem duplicar `00_Indice_MOC.md` ou `_index.json`).
 * 3. `action: "create"` -> Cria novas notas `.md` na pasta do Obsidian.
 */

function doPost(e) {
  var data = JSON.parse(e.postData.contents);

  // ID da sua pasta do Obsidian no Google Drive
  var folderId = "SEU_FOLDER_ID_DO_GOOGLE_DRIVE_AQUI";
  var folder = DriveApp.getFolderById(folderId);

  var action = data.action || "create";
  var filename = data.filename;
  var content = data.content || "";

  // 1. Leitura de arquivo (ex: recuperar _index.json na Vercel)
  if (action === "read") {
    var files = folder.getFilesByName(filename);
    if (files.hasNext()) {
      return ContentService.createTextOutput(files.next().getBlob().getDataAsString());
    }
    return ContentService.createTextOutput("NAO_ENCONTRADO");
  }

  // 2. Atualizacao sem duplicar (para 00_Indice_MOC.md e _index.json)
  var shouldOverwrite =
    data.overwrite === true ||
    action === "upsert" ||
    filename === "00_Indice_MOC.md" ||
    filename === "_index.json";

  if (shouldOverwrite) {
    var existingFiles = folder.getFilesByName(filename);
    if (existingFiles.hasNext()) {
      var file = existingFiles.next();
      file.setContent(content);
      return ContentService.createTextOutput("Atualizado: " + filename);
    }
  }

  // 3. Criacao padrao de nova nota
  folder.createFile(filename, content, MimeType.PLAIN_TEXT);
  return ContentService.createTextOutput("Criado: " + filename);
}
