"""Generated structural operation registry; do not edit by hand.

The authored overlay is the distributed source. The pinned CC BY-NC-SA
OpenAPI documentation was used only to verify these endpoint facts.
"""

from __future__ import annotations

from types import MappingProxyType

from .operation import NativeOperation

SOURCE_COMMIT = "e62f350cd7a896c45a0e025f44cb8915f5597854"
OVERLAY_SHA256 = "74a5de915e2a71466a4b852ab26926edbe1405de74c84358daaebde4f12877c8"

_OPERATIONS = {
    "CreatePost": NativeOperation(
        operation_id="CreatePost",
        method="POST",
        path_template="posts",
        success_statuses=(201,),
        body="required",
        source="api/v4/source/posts.yaml",
        source_sha256="83400ef682b755f3f3830dd4b622373b659bcc5b4d2d159756a735752b3b19ff",
    ),
    "DeletePost": NativeOperation(
        operation_id="DeletePost",
        method="DELETE",
        path_template="posts/{post_id}",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/posts.yaml",
        source_sha256="83400ef682b755f3f3830dd4b622373b659bcc5b4d2d159756a735752b3b19ff",
    ),
    "DeleteReaction": NativeOperation(
        operation_id="DeleteReaction",
        method="DELETE",
        path_template="users/{user_id}/posts/{post_id}/reactions/{emoji_name}",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/reactions.yaml",
        source_sha256="2eba6a5191114022b553e8f606cd3114bdb025875aea264438b5052d7a3b6d9d",
    ),
    "GetChannel": NativeOperation(
        operation_id="GetChannel",
        method="GET",
        path_template="channels/{channel_id}",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/channels.yaml",
        source_sha256="b2cfb3f61246da3a1e0f0a5f7e93f449012e9a8680b67c44f4f337d413364e81",
    ),
    "GetChannelByName": NativeOperation(
        operation_id="GetChannelByName",
        method="GET",
        path_template="teams/{team_id}/channels/name/{channel_name}",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/channels.yaml",
        source_sha256="b2cfb3f61246da3a1e0f0a5f7e93f449012e9a8680b67c44f4f337d413364e81",
    ),
    "GetPost": NativeOperation(
        operation_id="GetPost",
        method="GET",
        path_template="posts/{post_id}",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/posts.yaml",
        source_sha256="83400ef682b755f3f3830dd4b622373b659bcc5b4d2d159756a735752b3b19ff",
    ),
    "GetPostThread": NativeOperation(
        operation_id="GetPostThread",
        method="GET",
        path_template="posts/{post_id}/thread",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/posts.yaml",
        source_sha256="83400ef682b755f3f3830dd4b622373b659bcc5b4d2d159756a735752b3b19ff",
    ),
    "GetPostsForChannel": NativeOperation(
        operation_id="GetPostsForChannel",
        method="GET",
        path_template="channels/{channel_id}/posts",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/posts.yaml",
        source_sha256="83400ef682b755f3f3830dd4b622373b659bcc5b4d2d159756a735752b3b19ff",
    ),
    "GetPreferences": NativeOperation(
        operation_id="GetPreferences",
        method="GET",
        path_template="users/{user_id}/preferences",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/preferences.yaml",
        source_sha256="3bf3c9734d69bfdf20e1dd4e71158a65a81fca9dd64ee1703269b460111625a2",
    ),
    "GetReactions": NativeOperation(
        operation_id="GetReactions",
        method="GET",
        path_template="posts/{post_id}/reactions",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/reactions.yaml",
        source_sha256="2eba6a5191114022b553e8f606cd3114bdb025875aea264438b5052d7a3b6d9d",
    ),
    "GetTeam": NativeOperation(
        operation_id="GetTeam",
        method="GET",
        path_template="teams/{team_id}",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/teams.yaml",
        source_sha256="dc0723e5164d19b5d147ee9133db170fb83eedb3b884e320cd58ee36435cd3b3",
    ),
    "GetTeamByName": NativeOperation(
        operation_id="GetTeamByName",
        method="GET",
        path_template="teams/name/{name}",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/teams.yaml",
        source_sha256="dc0723e5164d19b5d147ee9133db170fb83eedb3b884e320cd58ee36435cd3b3",
    ),
    "GetUser": NativeOperation(
        operation_id="GetUser",
        method="GET",
        path_template="users/{user_id}",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/users.yaml",
        source_sha256="dcba0e1814e376e552dc7c9f0333b81d75aca8c95bfd17cf07947bdaf81fd9aa",
    ),
    "GetUserByUsername": NativeOperation(
        operation_id="GetUserByUsername",
        method="GET",
        path_template="users/username/{username}",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/users.yaml",
        source_sha256="dcba0e1814e376e552dc7c9f0333b81d75aca8c95bfd17cf07947bdaf81fd9aa",
    ),
    "GetUserStatus": NativeOperation(
        operation_id="GetUserStatus",
        method="GET",
        path_template="users/{user_id}/status",
        success_statuses=(200,),
        body="forbidden",
        source="api/v4/source/status.yaml",
        source_sha256="e1adcf4a377c13618d7229f5319bfaceeea1879810d17cec5887f88b4032cde7",
    ),
    "PatchPost": NativeOperation(
        operation_id="PatchPost",
        method="PUT",
        path_template="posts/{post_id}/patch",
        success_statuses=(200,),
        body="required",
        source="api/v4/source/posts.yaml",
        source_sha256="83400ef682b755f3f3830dd4b622373b659bcc5b4d2d159756a735752b3b19ff",
    ),
    "SaveReaction": NativeOperation(
        operation_id="SaveReaction",
        method="POST",
        path_template="reactions",
        success_statuses=(200, 201),
        body="required",
        source="api/v4/source/reactions.yaml",
        source_sha256="2eba6a5191114022b553e8f606cd3114bdb025875aea264438b5052d7a3b6d9d",
    ),
}

OPERATIONS = MappingProxyType(_OPERATIONS)
