import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

/// Multiplexed realtime event stream from the engine, with auto-reconnect.
/// Every outgoing message doubles as a heartbeat for the engine watchdog.
class WsClient {
  final int port;
  final _controller = StreamController<Map<String, dynamic>>.broadcast();
  WebSocketChannel? _channel;
  Timer? _pingTimer;
  bool _closed = false;
  bool _reconnectScheduled = false;

  WsClient(this.port) {
    _connect();
  }

  Stream<Map<String, dynamic>> get events => _controller.stream;

  void _connect() {
    if (_closed) return;
    _reconnectScheduled = false;
    // Make sure a previous half-dead channel can never coexist with the new
    // one (duplicate events / connection storms).
    try {
      _channel?.sink.close();
    } catch (_) {}
    try {
      final channel = WebSocketChannel.connect(
          Uri.parse('ws://127.0.0.1:$port/events'));
      _channel = channel;
      _pingTimer?.cancel();
      _pingTimer = Timer.periodic(const Duration(seconds: 20), (_) {
        if (!identical(_channel, channel)) return;
        try {
          channel.sink.add('ping');
        } catch (_) {}
      });
      channel.stream.listen(
        (message) {
          if (_closed || !identical(_channel, channel)) return;
          try {
            _controller.add(
                Map<String, dynamic>.from(jsonDecode(message as String)));
          } catch (_) {}
        },
        // onError and onDone both fire on a dropped socket; the guard in
        // _scheduleReconnect stops that from doubling the connections.
        onDone: () {
          if (identical(_channel, channel)) _scheduleReconnect();
        },
        onError: (_) {
          if (identical(_channel, channel)) _scheduleReconnect();
        },
      );
      channel.sink.add('hello');
    } catch (_) {
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    if (_closed || _reconnectScheduled) return;
    _reconnectScheduled = true;
    Future<void>.delayed(const Duration(seconds: 2), _connect);
  }

  void dispose() {
    _closed = true;
    _pingTimer?.cancel();
    _channel?.sink.close();
    _controller.close();
  }
}
