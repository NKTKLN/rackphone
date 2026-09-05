/// Failures translated into states the UI can explain without seeing secrets.
library;

sealed class GatewayException implements Exception {
  const GatewayException();
}

final class GatewayAuthException extends GatewayException {
  const GatewayAuthException(this.reason);

  final String reason;
}

final class GatewayLockedException extends GatewayException {
  const GatewayLockedException({required this.retryAfter});

  final Duration retryAfter;
}

final class GatewayForbiddenException extends GatewayException {
  const GatewayForbiddenException(this.reason);

  final String reason;
}

final class GatewayUnavailableException extends GatewayException {
  const GatewayUnavailableException();
}

final class GatewayNetworkException extends GatewayException {
  const GatewayNetworkException(this.cause);

  final Object cause;
}

final class GatewayProtocolException extends GatewayException {
  const GatewayProtocolException(this.message);

  final String message;
}
