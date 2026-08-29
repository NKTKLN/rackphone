/// Fixtures shared by the tests, kept in the shape the native side actually
/// writes so a parser change has to survive a real document.
///
/// Two SIMs by default, because that is the case the per-subscription
/// bookkeeping exists for and the one a single-SIM fixture would never catch.
library;

/// A realistic `status.json`.
Map<String, dynamic> statusJson({
  bool ready = true,
  bool sendSms = true,
  bool receiveSms = true,
  bool readCallLog = true,
  bool readPhoneState = true,
  bool batteryExempt = true,
  int lastSuccessMs = 1700000000000,
  bool secondSimResolves = true,
  String subs = 'all',
  int pending = 0,
  int dropped = 0,
  bool collectSms = true,
  double? balance,
}) => <String, dynamic>{
  'schema': 1,
  'package': 'com.nktkln.rackphone.companion',
  'generated_ms': 1700000600000,
  'ready': ready,
  'token_set': true,
  'permissions': <String, dynamic>{
    'send_sms': sendSms,
    'receive_sms': receiveSms,
    'read_call_log': readCallLog,
    'read_phone_state': readPhoneState,
  },
  'power': <String, dynamic>{
    'battery_exempt': batteryExempt,
    'standby_bucket': batteryExempt ? 'active' : 'restricted',
  },
  'sub_id': -1,
  'sims': <dynamic>[
    <String, dynamic>{
      'sub_id': 1,
      'slot': 0,
      'carrier': 'beeline',
      'label': 'beeline',
      'is_default_sms': true,
      'number': '+79001234567',
    },
    <String, dynamic>{
      'sub_id': 2,
      'slot': 1,
      'carrier': 'Yota',
      'label': 'Yota',
      'is_default_sms': false,
      'number': secondSimResolves ? '+79005550101' : '',
    },
  ],
  'keepalive': <String, dynamic>{
    'enabled': true,
    'to': 'self',
    'subs': subs,
    'interval_hours': 720,
    'body_chars': 19,
    'next_due_ms': lastSuccessMs + 720 * 3600000,
    'targets': <dynamic>[
      <String, dynamic>{
        'sub_id': 1,
        'to': '+79001234567',
        'resolves': true,
        'last_success_ms': lastSuccessMs,
        'next_due_ms': lastSuccessMs + 720 * 3600000,
      },
      <String, dynamic>{
        'sub_id': 2,
        'to': secondSimResolves ? '+79005550101' : '',
        'resolves': secondSimResolves,
        'last_success_ms': 0,
        'next_due_ms': lastSuccessMs + 800 * 3600000,
      },
    ],
  },
  'balances': <dynamic>[
    if (balance != null)
      <String, dynamic>{
        'sub_id': 1,
        'amount': balance,
        'text': 'Balans: $balance r',
        'checked_ms': lastSuccessMs,
      },
  ],
  'inbox': <String, dynamic>{
    'collect_sms': collectSms,
    'collect_calls': true,
    'include_body': true,
    'cap': 2000,
    'pending': pending,
    'dropped': dropped,
  },
  'counters': <String, dynamic>{
    'sent_ok': 4,
    'sent_failed': 1,
    'rejected': 0,
    'pending': 0,
  },
  'last_send': <String, dynamic>{
    'id': 'ka-1a2b3c4d',
    'ts': lastSuccessMs,
    'to': '+79001234567',
    'source': 'keepalive',
    'status': 'ok',
    'body_chars': 19,
  },
};
