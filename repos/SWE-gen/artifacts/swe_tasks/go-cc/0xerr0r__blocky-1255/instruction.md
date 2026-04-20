The `log.privacy` configuration option is not hiding domain names in DNS query logs as expected. When `log.privacy: true` is set in the configuration, domain names should be obfuscated in log output, but they are currently being logged in plain text.

Before upgrading (when privacy was working correctly), log entries showed obfuscated domains like:
```
2023-08-19 10:26:53     127.0.0.1       localhost       0       CACHED  * (**.*.*****.***.)     ***** (*.*****.*********.***.), ***** (*****.****.******.***.), * (*.**.***.***)      NOERROR
```

After the upgrade, log entries now show unobfuscated domain names:
```
2023-08-19 10:48:13     127.0.0.1       localhost       0       CACHED  r3.o.lencr.org. ***** (*.*****.*********.***.), ***** (*****.****.******.***.), * (*.**.***.**), * (*.**.***.**)        NOERROR
```

The `log.privacy: true` setting should obfuscate all domain names in DNS question and answer logging throughout the application. Some log statements are logging DNS question/answer data without applying the privacy obfuscation that the configuration option is supposed to enforce.

Expected behavior: When `log.privacy: true` is configured, all domain names in logs should be obfuscated/masked.
Actual behavior: Domain names in certain log statements are being logged in plain text despite the privacy setting being enabled.