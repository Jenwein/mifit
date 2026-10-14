"""The Xiaomi FDS file chain behind a workout's detail: per-second samples and a GPS track.

This package is the Xiaomi-specific seam (design §12.3): everything that knows how
Mi Fitness names, fetches, decrypts and lays out these files lives here. The neutral
models live in ``models/``; the mapping between the two lives in ``domains/``.

The chain, as the app runs it for one workout of the list read:

1. ``ids`` names each file (record, GPS) from the workout's list row (``FileRef``).
2. ``gen_download_url`` (a signed ``Transport`` call) answers one short-lived,
   presigned URL and one AES key per file.
3. ``download`` fetches each URL with a separate, session-less client.
4. ``cipher`` turns a download body into the plain file with the key.
5. ``binary`` and ``layouts`` read the plain file's header and fields.

Low-level use, for the plain bytes of a file: ``urls.request_urls(t, sid, refs)``
sends one signed request for the ``FileRef`` list ``refs`` and gives a map from
``ids.server_key(ref)`` to an ``FdsEntry``; ``download.FdsDownloader().fetch(entry.url)``
gives that file's encoded body; ``cipher.decrypt_body(body, entry.key_b64)`` gives the
plain file. An entry whose ``key_b64`` is ``None`` names a file the service does not
have: do not download it. Nothing is cached or written to disk, and a link expires
about 30 minutes after the request.

Safety rules for every module here:

* Never output the presigned URL, ``obj_key``, ``obj_name``, ``expires_time``, the
  source id (``sid``/``did``), a suffix or a server key: not in records, ``raw``,
  notes, exception messages or reprs. A note may name an HTTP status, an exception
  type, a host name, a format or version number, or a workout's position.
* A download carries no cookie, header or credential of the session, never follows a
  redirect, accepts only ``https`` hosts under ``.fds.api.xiaomi.com`` and HTTP 200,
  and stops at a size cap.
* Downloaded bytes are untrusted: bounds-checked reads only, no recursion, no
  ``eval``, no disk writes and no cache.
* Times are true Unix seconds; the app's display shift is never applied.
"""
