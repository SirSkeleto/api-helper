from flask import Flask, request, current_app
from threading import Lock
from cachetools import TTLCache
import requests
from .constants import *
import urllib.parse
import json
import sqlite3

def update_headers():
    account = current_app.state["twitter"]["accounts"][current_app.state["twitter"]["idx"]]
    current_app.state["twitter"]["cookies"] = {
        "auth_token": account["auth_token"],
        "ct0": account["csrf_token"]
    }
    current_app.state["twitter"]["headers"] = {
        "authorization": account["bearer_token"],
        "x-csrf-token": account["csrf_token"]
    }

def setup():
    res = current_app.con.execute("SELECT user_id, auth_token, csrf_token, bearer_token FROM twitter_accounts ORDER BY user_id asc").fetchall()
    if(len(res) == 0):
        print("No twitter accounts, so not hosting /twitter/*")
        return
    current_app.add_url_rule("/twitter/media", view_func=twitter_media)
    current_app.add_url_rule("/twitter/tweet", view_func=twitter_tweet)
    state = {
        "accounts": res,
        "idx": 0,
        "user_ids": {},
        "mutex": Lock(),
        "cache": TTLCache(maxsize = 2000, ttl = 900),
        "recache": {}
    }
    current_app.state["twitter"] = state
    update_headers()

def _request(url, params, local_idx):
    retries = 0
    while True:
        cookies = None
        headers = None
        with current_app.state["twitter"]["mutex"]:
            cookies = current_app.state["twitter"]["cookies"]
            headers = current_app.state["twitter"]["headers"]
        response = requests.get(
            url,
            params=params,
            cookies=cookies,
            headers=headers
        )
        if response.status_code == 429:
            if(retries < len(current_app.state["twitter"]["accounts"])):
                retries += 1
                with current_app.state["twitter"]["mutex"]:
                    if(local_idx == current_app.state["twitter"]["idx"]):
                        local_idx = (local_idx + 1) % len(current_app.state["twitter"]["accounts"])
                        current_app.state["twitter"]["idx"] = local_idx
                        update_headers()
                    else:
                        local_idx = current_app.state["twitter"]["idx"]
        else:
            return response

def twitter_media():
    local_idx = current_app.state["twitter"]["idx"]
    username = request.args["username"]
    if username not in current_app.state["twitter"]["user_ids"]:
        response = _request(
            f"https://api.twitter.com/graphql/{screen_name_key}/UserByScreenName",
            urllib.parse.urlencode({
                "variables": json.dumps(
                    screen_name_variables |
                    {"screen_name": username}
                ),
                "features" : json.dumps(screen_name_features)
            }
            ),
            local_idx
        )
        if response.ok:
            try:
                current_app.state["twitter"]["user_ids"][username] = response.json()["data"]["user"]["result"]["rest_id"]
            except Exception as e:
                return {"note": str(e)}, response.status_code
        else:
            return {"note": str(response.status_code)}, response.status_code
        local_idx = current_app.state["twitter"]["idx"]

    response = _request(
        f"https://api.twitter.com/graphql/{user_media_key}/UserMedia",
        urllib.parse.urlencode({
            "variables": json.dumps(
                user_media_variables |
                {"userId": current_app.state["twitter"]["user_ids"][username]} |
                ({"cursor": request.args["cursor"]} if "cursor" in request.args else {})
            ),
            "features" : json.dumps(user_media_features)
        }),
        local_idx
    )
    if response.ok:
        if "debug" in request.args: return response.json()
        entries = response.json()["data"]["user"]["result"]["timeline_v2"]["timeline"]["instructions"][0]["entries"]
        tweet_entries = [entry for entry in entries if (entry["content"]["__typename"] == "TimelineTimelineItem" and entry["content"]["itemContent"]["tweet_results"])]
        bottom_cursor = entries[-1]["content"]["value"]
        if "locked" in request.args:
            for entry in tweet_entries:
                tweet_id = entry["entryId"][6:]
                if "result" not in entry["content"]["itemContent"]["tweet_results"]: continue
                tweet = entry["content"]["itemContent"]["tweet_results"]["result"]
                match tweet["__typename"]:
                    case "Tweet": 
                        current_app.state["twitter"]["cache"][tweet_id] = tweet
                        current_app.state["twitter"]["recache"][tweet_id] = (current_app.state["twitter"]["user_ids"][username], request.args["cursor"] if "cursor" in request.args else None)
                    case "TweetWithVisibilityResults":
                        current_app.state["twitter"]["cache"][tweet_id] = tweet["tweet"]
                        current_app.state["twitter"]["recache"][tweet_id] = (current_app.state["twitter"]["user_ids"][username], request.args["cursor"] if "cursor" in request.args else None)
                    case "TweetUnavailable":
                        current_app.log(f"error reading protected tweet: {tweet_id}")
                        return {"note": result["reason"]}, response.status_code
                    case _:
                        print("Unexpected json structure in response!")
                        print(response.json())
        return {"tweet_ids": [entry["entryId"][6:] for entry in tweet_entries], "next_page": f"media?username={username}&cursor={bottom_cursor}"}#entryId contains rest_id as a substring after 'tweet-'
    else:
        return {"note": str(response.status_code)}, response.status_code

def twitter_tweet():
    tweet_id = request.args["tweet"]
    local_idx = current_app.state["twitter"]["idx"]
    if tweet_id in current_app.state["twitter"]["cache"]: return current_app.state["twitter"]["cache"].pop(tweet_id)
    elif tweet_id in current_app.state["twitter"]["recache"]:
        user_id, cursor = current_app.state["twitter"]["recache"][tweet_id]
        response = _request(
            f"https://api.twitter.com/graphql/{user_media_key}/UserMedia",
            urllib.parse.urlencode({
                "variables": json.dumps(
                    user_media_variables |
                    {"userId": user_id} |
                    ({"cursor": cursor} if cursor else {})
                ),
                "features" : json.dumps(user_media_features)
            }),
            local_idx
        )
        if response.ok:
            entries = response.json()["data"]["user"]["result"]["timeline_v2"]["timeline"]["instructions"][0]["entries"]
            tweet_entries = [entry for entry in entries if (entry["content"]["__typename"] == "TimelineTimelineItem" and entry["content"]["itemContent"]["tweet_results"])]
            bottom_cursor = entries[-1]["content"]["value"]
            for entry in tweet_entries:
                if "result" not in entry["content"]["itemContent"]["tweet_results"]: continue
                tweet = entry["content"]["itemContent"]["tweet_results"]["result"]
                match tweet["__typename"]:
                    case "Tweet": 
                        print("e")
                        current_app.state["twitter"]["cache"][tweet_id] = tweet
                    case "TweetWithVisibilityResults":
                        print("f")
                        current_app.state["twitter"]["cache"][tweet_id] = tweet["tweet"]
                    case "TweetUnavailable":
                        current_app.log(f"error reading protected tweet: {tweet_id}")
                    case _:
                        print("Unexpected json structure in response!")
                        print(response.json())
        if tweet_id in current_app.state["twitter"]["cache"]: return current_app.state["twitter"]["cache"].pop(tweet_id)
    response = _request(
        f"https://api.twitter.com/graphql/{tweet_key}/TweetResultByRestId",
        urllib.parse.urlencode({
            "variables": json.dumps(
                tweet_variables |
                {"tweetId": tweet_id}
            ),
            "features" : json.dumps(tweet_features)
        }),
        local_idx
    )
    if response.ok:
        if "debug" in request.args: return response.json()
        try:
            result = response.json()["data"]["tweetResult"]["result"]
            match result["__typename"]:
                case "Tweet": return result
                case "TweetWithVisibilityResults": return result["tweet"]
                case "TweetUnavailable":
                    current_app.log(f"error reading protected tweet: {tweet_id}")
                    return {"note": result["reason"]}, response.status_code
                case _:
                    print("Unexpected json structure in response!")
                    print(response.json())
                    return response.json()
        except:
            print("Unexpected json structure in response!")
            print(response.json())
            return response.json()
    else:
        return {"note": str(response.status_code)}, response.status_code