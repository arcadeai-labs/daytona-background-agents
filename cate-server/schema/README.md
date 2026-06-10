# Webhook Schema

> ⚠️ **Beta**: This schema is still in beta and may be changed without notice.

This directory contains the OpenAPI schema and generated Go types for the CATE Webhook API.

## Files

- `schema.yaml` - OpenAPI 3.0 specification for the webhook contract
- `schema.gen.go` - Generated Go types, client, and server interface
- `cfg.yaml` - Configuration for oapi-codegen

## Regenerating

If you modify `schema.yaml`, regenerate the Go code:

```bash
go install github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@latest
oapi-codegen -config cfg.yaml schema.yaml
```
