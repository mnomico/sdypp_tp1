"""Script de benchmark comparativo: JSON over TCP vs Protocol Buffers over gRPC."""

import json
import socket
import time
import uuid

import grpc

from comun import mensajes
from comun.protocolo import LectorDeMensajes, enviar_mensaje
from hit5.nodo_c import NodoC as NodoCJson
from hit8.nodo_c import NodoC as NodoCGrpc
from hit8.proto import servicio_pb2, servicio_pb2_grpc


def medir_tamano_mensajes():
    saludo_id = str(uuid.uuid4())
    resp_id = str(uuid.uuid4())
    ts = "2026-09-03T16:00:00.000000+00:00"

    # 1. JSON
    saludo_json = {
        "version": 1,
        "id": saludo_id,
        "tipo": "saludo",
        "origen": "C1",
        "contenido": "Hola, soy C1",
        "timestamp": ts,
    }
    resp_json = {
        "version": 1,
        "id": resp_id,
        "tipo": "respuesta",
        "origen": "C2",
        "contenido": "Hola C1, soy C2. Recibi tu saludo.",
        "en_respuesta_a": saludo_id,
        "timestamp": ts,
    }

    raw_saludo_json = json.dumps(saludo_json, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    raw_resp_json = json.dumps(resp_json, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    # 2. Protobuf
    saludo_proto = servicio_pb2.MensajeSaludo(
        version=1,
        id=saludo_id,
        tipo="saludo",
        origen="C1",
        contenido="Hola, soy C1",
        timestamp=ts,
    )
    resp_proto = servicio_pb2.MensajeRespuesta(
        version=1,
        id=resp_id,
        tipo="respuesta",
        origen="C2",
        contenido="Hola C1, soy C2. Recibi tu saludo.",
        en_respuesta_a=saludo_id,
        timestamp=ts,
    )

    raw_saludo_proto = saludo_proto.SerializeToString()
    raw_resp_proto = resp_proto.SerializeToString()

    return {
        "json_saludo_bytes": len(raw_saludo_json),
        "json_resp_bytes": len(raw_resp_json),
        "json_total_bytes": len(raw_saludo_json) + len(raw_resp_json),
        "proto_saludo_bytes": len(raw_saludo_proto),
        "proto_resp_bytes": len(raw_resp_proto),
        "proto_total_bytes": len(raw_saludo_proto) + len(raw_resp_proto),
    }


def medir_latencias(iteraciones=200):
    import logging
    logger = logging.getLogger("bench")
    logger.addHandler(logging.NullHandler())

    # Benchmark JSON / Raw TCP
    nodo_json = NodoCJson("127.0.0.1", 0, logger, nombre="C-json")
    nodo_json.iniciar(espera_inicial=0.01)
    port_json = nodo_json.puerto

    latencias_json = []
    # Reutilizando conexión TCP (sesión)
    with socket.create_connection(("127.0.0.1", port_json)) as sock:
        lector = LectorDeMensajes(sock)
        for _ in range(iteraciones):
            t0 = time.perf_counter()
            msg = mensajes.crear_saludo("C-bench")
            enviar_mensaje(sock, mensajes.serializar(msg))
            resp = mensajes.deserializar(lector.leer_mensaje())
            t1 = time.perf_counter()
            latencias_json.append((t1 - t0) * 1000)  # ms

    nodo_json.detener()

    # Benchmark gRPC / Protobuf
    nodo_grpc = NodoCGrpc("127.0.0.1", 0, logger, nombre="C-grpc")
    nodo_grpc.iniciar()
    port_grpc = nodo_grpc.puerto

    latencias_grpc = []
    with grpc.insecure_channel(f"127.0.0.1:{port_grpc}") as channel:
        stub = servicio_pb2_grpc.MensajeriaCStub(channel)
        for _ in range(iteraciones):
            t0 = time.perf_counter()
            req = servicio_pb2.MensajeSaludo(
                version=1,
                id=str(uuid.uuid4()),
                tipo="saludo",
                origen="C-bench",
                contenido="Hola, soy C-bench",
                timestamp="2026-09-03T16:00:00+00:00",
            )
            resp = stub.Saludar(req)
            t1 = time.perf_counter()
            latencias_grpc.append((t1 - t0) * 1000)  # ms

    nodo_grpc.detener()

    return {
        "json_avg_ms": sum(latencias_json) / len(latencias_json),
        "json_min_ms": min(latencias_json),
        "json_max_ms": max(latencias_json),
        "grpc_avg_ms": sum(latencias_grpc) / len(latencias_grpc),
        "grpc_min_ms": min(latencias_grpc),
        "grpc_max_ms": max(latencias_grpc),
    }


if __name__ == "__main__":
    tam = medir_tamano_mensajes()
    print("--- Tamaño de Mensajes (Bytes) ---")
    print(f"JSON Saludo: {tam['json_saludo_bytes']} B | Protobuf Saludo: {tam['proto_saludo_bytes']} B (-{(1 - tam['proto_saludo_bytes']/tam['json_saludo_bytes'])*100:.1f}%)")
    print(f"JSON Respuesta: {tam['json_resp_bytes']} B | Protobuf Respuesta: {tam['proto_resp_bytes']} B (-{(1 - tam['proto_resp_bytes']/tam['json_resp_bytes'])*100:.1f}%)")
    print(f"JSON Total RTT: {tam['json_total_bytes']} B | Protobuf Total RTT: {tam['proto_total_bytes']} B (-{(1 - tam['proto_total_bytes']/tam['json_total_bytes'])*100:.1f}%)")

    print("\n--- Latencia (ms) en 200 llamadas locales ---")
    lat = medir_latencias(200)
    print(f"JSON/TCP: avg={lat['json_avg_ms']:.3f}ms | min={lat['json_min_ms']:.3f}ms | max={lat['json_max_ms']:.3f}ms")
    print(f"gRPC/HTTP2: avg={lat['grpc_avg_ms']:.3f}ms | min={lat['grpc_min_ms']:.3f}ms | max={lat['grpc_max_ms']:.3f}ms")
