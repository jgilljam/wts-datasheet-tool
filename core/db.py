"""Supabase-Client als gecachte Resource.

Eine Instanz pro Session — `@st.cache_resource` gibt allen Pages und allen
Reruns den gleichen Client. Wir nutzen den **Secret Key** (RLS-bypass) bis
Google-OIDC + Supabase-Auth-Mapping in Task #5 dazukommt.
"""

import streamlit as st
from supabase import Client, create_client


@st.cache_resource
def supabase() -> Client:
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_SECRET_KEY"],
    )
