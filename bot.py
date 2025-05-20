def upload_file_to_bitrix(file_url: str, folder_id: int = PARENT_ID) -> Optional[int]:
    local_filename = None
    try:
        # Скачиваем файл с Telegram CDN
        local_filename = file_url.split('/')[-1].split('?')[0]
        with requests.get(file_url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            with open(local_filename, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

        # Загружаем в Bitrix24 через disk.folder.uploadfile.json
        upload_url = BITRIX_WEBHOOK_URL.replace('task.item.add.json', 'disk.folder.uploadfile.json')

        with open(local_filename, 'rb') as f:
            files = {'file': (local_filename, f)}
            data = {'id': folder_id}

            response = requests.post(upload_url, files=files, data=data, timeout=30)
            response.raise_for_status()
            result = response.json()

            logger.info(f"Bitrix24 upload response: {result}")

            # Корректное извлечение ID вложения
            file_info = result.get("result", {})
            if "ATTACHED_OBJECT" in file_info:
                return file_info["ATTACHED_OBJECT"]["ID"]
            if "attachedId" in file_info:
                return file_info["attachedId"]
            if "ID" in file_info:
                return file_info["ID"]

            logger.error(f"No file ID in response: {result}")
            return None

    except Exception as e:
        logger.error(f"Error uploading file to Bitrix24: {e}")
        return None
    finally:
        if local_filename and os.path.exists(local_filename):
            try:
                os.remove(local_filename)
            except Exception as e:
                logger.error(f"Error removing temp file: {e}")
