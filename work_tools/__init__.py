"""Provide Elysia Work capabilities without shadowing vendor runtime namespaces.

The reviewed singing worker imports its own package named tools in a separate
process. This distinctive namespace also keeps test collection from preloading
that vendor name and weakening the worker's source-origin checks.
"""
