export interface paths {
    "/api/v1/account/deletion-previews": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create Account Deletion Preview
         * @description Issue the one-time account-deletion confirmation secret exactly once.
         */
        post: operations["create_account_deletion_preview_api_v1_account_deletion_previews_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/account/deletion-requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Confirm Account Deletion */
        post: operations["confirm_account_deletion_api_v1_account_deletion_requests_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/account/deletion-requests/{deletion_request_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Account Deletion */
        get: operations["get_account_deletion_api_v1_account_deletion_requests__deletion_request_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/account/deletion-requests/{deletion_request_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry Account Deletion Target */
        post: operations["retry_account_deletion_target_api_v1_account_deletion_requests__deletion_request_id__retry_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/activities": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Activities */
        get: operations["list_activities_api_v1_activities_get"];
        put?: never;
        /** Create Activity */
        post: operations["create_activity_api_v1_activities_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/activities/{activity_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Activity */
        get: operations["get_activity_api_v1_activities__activity_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Activity */
        patch: operations["update_activity_api_v1_activities__activity_id__patch"];
        trace?: never;
    };
    "/api/v1/activities/{activity_id}/complete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Complete Activity */
        post: operations["complete_activity_api_v1_activities__activity_id__complete_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/activities/{activity_id}/episodes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Episodes */
        get: operations["list_episodes_api_v1_activities__activity_id__episodes_get"];
        put?: never;
        /** Create Episode */
        post: operations["create_episode_api_v1_activities__activity_id__episodes_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/activities/{activity_id}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Activity Versions */
        get: operations["list_activity_versions_api_v1_activities__activity_id__versions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/activities/{activity_id}/versions/{version_no}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Activity Version */
        get: operations["get_activity_version_api_v1_activities__activity_id__versions__version_no__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/application-projects": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Projects */
        get: operations["list_projects_api_v1_application_projects_get"];
        put?: never;
        /** Create Project */
        post: operations["create_project_api_v1_application_projects_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/application-projects/{project_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Project */
        get: operations["get_project_api_v1_application_projects__project_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Project */
        patch: operations["update_project_api_v1_application_projects__project_id__patch"];
        trace?: never;
    };
    "/api/v1/application-projects/{project_id}/job-posting": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Link Existing Job Posting */
        post: operations["link_existing_job_posting_api_v1_application_projects__project_id__job_posting_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/application-projects/{project_id}/questions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Questions */
        get: operations["list_questions_api_v1_application_projects__project_id__questions_get"];
        put?: never;
        /** Create Question */
        post: operations["create_question_api_v1_application_projects__project_id__questions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/application-projects/{project_id}/source-collections": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Latest Source Collection */
        get: operations["get_latest_source_collection_api_v1_application_projects__project_id__source_collections_get"];
        put?: never;
        /** Create Source Collection */
        post: operations["create_source_collection_api_v1_application_projects__project_id__source_collections_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/application-projects/{project_id}/source-collections/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Source Collection Progress */
        get: operations["get_source_collection_progress_api_v1_application_projects__project_id__source_collections__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/application-projects/{project_id}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Project Versions */
        get: operations["list_project_versions_api_v1_application_projects__project_id__versions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/google/callback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Complete Google Login */
        get: operations["complete_google_login_api_v1_auth_google_callback_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/google/start": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Start Google Login */
        get: operations["start_google_login_api_v1_auth_google_start_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Logout Epick Session */
        post: operations["logout_epick_session_api_v1_auth_logout_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/refresh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Refresh Epick Access Token */
        post: operations["refresh_epick_access_token_api_v1_auth_refresh_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/companies": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Companies */
        get: operations["list_companies_api_v1_companies_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/companies/{company_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Company */
        get: operations["get_company_api_v1_companies__company_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/companies/{company_id}/job-postings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Company Job Postings */
        get: operations["list_company_job_postings_api_v1_companies__company_id__job_postings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/consents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Consents */
        get: operations["get_consents_api_v1_consents_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/consents/analytics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Analytics Consent */
        patch: operations["update_analytics_consent_api_v1_consents_analytics_patch"];
        trace?: never;
    };
    "/api/v1/data-retention": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Data Retention
         * @description No selectable retention policy is published before the governance decision.
         */
        get: operations["get_data_retention_api_v1_data_retention_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update Data Retention
         * @description Reject writes until a product-approved selectable policy exists.
         */
        patch: operations["update_data_retention_api_v1_data_retention_patch"];
        trace?: never;
    };
    "/api/v1/episodes/{episode_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Episode */
        get: operations["get_episode_api_v1_episodes__episode_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Episode */
        patch: operations["update_episode_api_v1_episodes__episode_id__patch"];
        trace?: never;
    };
    "/api/v1/episodes/{episode_id}/complete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Complete Episode */
        post: operations["complete_episode_api_v1_episodes__episode_id__complete_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/episodes/{episode_id}/inference-jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Start Inference Job
         * @description Keep the public endpoint explicit until the engine dispatch gate is approved.
         */
        post: operations["start_inference_job_api_v1_episodes__episode_id__inference_jobs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/episodes/{episode_id}/inferences": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Inferences */
        get: operations["list_inferences_api_v1_episodes__episode_id__inferences_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/episodes/{episode_id}/inferences/{inference_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Decide Inference */
        patch: operations["decide_inference_api_v1_episodes__episode_id__inferences__inference_id__patch"];
        trace?: never;
    };
    "/api/v1/episodes/{episode_id}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Episode Versions */
        get: operations["list_episode_versions_api_v1_episodes__episode_id__versions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/episodes/{episode_id}/versions/{version_no}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Episode Version */
        get: operations["get_episode_version_api_v1_episodes__episode_id__versions__version_no__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/experience-exclusions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Experience Exclusions */
        get: operations["list_experience_exclusions_api_v1_experience_exclusions_get"];
        put?: never;
        /** Create Experience Exclusion */
        post: operations["create_experience_exclusion_api_v1_experience_exclusions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/experience-exclusions/{exclusion_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Revoke Experience Exclusion */
        delete: operations["revoke_experience_exclusion_api_v1_experience_exclusions__exclusion_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/experiences/duplicates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Duplicate Suggestions */
        get: operations["list_duplicate_suggestions_api_v1_experiences_duplicates_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/experiences/duplicates/{duplicate_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Decide Duplicate Suggestion */
        patch: operations["decide_duplicate_suggestion_api_v1_experiences_duplicates__duplicate_id__patch"];
        trace?: never;
    };
    "/api/v1/feedback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Feedback */
        post: operations["create_feedback_api_v1_feedback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/home": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Home */
        get: operations["get_home_api_v1_home_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Jobs */
        get: operations["list_jobs_api_v1_jobs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Job */
        get: operations["get_job_api_v1_jobs__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jobs/{job_id}/actions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Submit Job Action */
        post: operations["submit_job_action_api_v1_jobs__job_id__actions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jobs/{job_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel Job */
        post: operations["cancel_job_api_v1_jobs__job_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jobs/{job_id}/checkpoint": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Job Checkpoint */
        get: operations["get_job_checkpoint_api_v1_jobs__job_id__checkpoint_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jobs/{job_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry Job */
        post: operations["retry_job_api_v1_jobs__job_id__retry_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/notifications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Notifications */
        get: operations["list_notifications_api_v1_notifications_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/notifications/{notification_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Notification */
        patch: operations["update_notification_api_v1_notifications__notification_id__patch"];
        trace?: never;
    };
    "/api/v1/questions/{question_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Question */
        get: operations["get_question_api_v1_questions__question_id__get"];
        put?: never;
        post?: never;
        /** Archive Question */
        delete: operations["archive_question_api_v1_questions__question_id__delete"];
        options?: never;
        head?: never;
        /** Update Question */
        patch: operations["update_question_api_v1_questions__question_id__patch"];
        trace?: never;
    };
    "/api/v1/questions/{question_id}/recommendation-runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Recommendation Run */
        post: operations["create_recommendation_run_api_v1_questions__question_id__recommendation_runs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/questions/{question_id}/selection": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Current Material Selection */
        get: operations["get_current_material_selection_api_v1_questions__question_id__selection_get"];
        put?: never;
        post?: never;
        /** Clear Current Material Selection */
        delete: operations["clear_current_material_selection_api_v1_questions__question_id__selection_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/questions/{question_id}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Question Versions */
        get: operations["list_question_versions_api_v1_questions__question_id__versions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/recommendation-candidates/{candidate_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Recommendation Candidate */
        get: operations["get_recommendation_candidate_api_v1_recommendation_candidates__candidate_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/recommendation-candidates/{candidate_id}/select": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Select Recommendation Candidate */
        post: operations["select_recommendation_candidate_api_v1_recommendation_candidates__candidate_id__select_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/recommendation-preferences": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Recommendation Preferences */
        get: operations["get_recommendation_preferences_api_v1_recommendation_preferences_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Recommendation Preferences */
        patch: operations["update_recommendation_preferences_api_v1_recommendation_preferences_patch"];
        trace?: never;
    };
    "/api/v1/recommendation-runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Recommendation Run */
        get: operations["get_recommendation_run_api_v1_recommendation_runs__run_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/recommendation-runs/{run_id}/candidates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Recommendation Candidates */
        get: operations["list_recommendation_candidates_api_v1_recommendation_runs__run_id__candidates_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/recommendation-runs/{run_id}/feedback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Run Feedback */
        post: operations["create_run_feedback_api_v1_recommendation_runs__run_id__feedback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/recommendation-runs/{run_id}/missing-candidate-feedback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Missing Candidate Feedback */
        post: operations["create_missing_candidate_feedback_api_v1_recommendation_runs__run_id__missing_candidate_feedback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/resume-items": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Resume Items */
        get: operations["list_resume_items_api_v1_resume_items_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Settings */
        get: operations["get_settings_api_v1_settings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Settings */
        patch: operations["update_settings_api_v1_settings_patch"];
        trace?: never;
    };
    "/api/v1/users/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Current User */
        get: operations["get_current_user_api_v1_users_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 프로세스 liveness 확인
         * @description FastAPI 프로세스가 실행 중인지
         */
        get: operations["health_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health/ready": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 데이터베이스 readiness 확인
         * @description PostgreSQL에 연결할 수 있는지 체크
         */
        get: operations["readiness_health_ready_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AccessTokenResponse */
        AccessTokenResponse: {
            /** Access Token */
            access_token: string;
            /** Expires In */
            expires_in: number;
            /**
             * Token Type
             * @default Bearer
             * @constant
             */
            token_type: "Bearer";
        };
        /** AccountDeletionConfirmRequest */
        AccountDeletionConfirmRequest: {
            /**
             * Confirmation
             * @constant
             */
            confirmation: "DELETE";
            /**
             * Deletion Request Id
             * Format: uuid
             */
            deletion_request_id: string;
            /** Preview Token */
            preview_token: string;
        };
        /**
         * AccountDeletionPreviewResponse
         * @description The opaque confirmation secret is returned once and never persisted in API replay data.
         */
        AccountDeletionPreviewResponse: {
            /**
             * Deletion Request Id
             * Format: uuid
             */
            deletion_request_id: string;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /** Preview Token */
            preview_token: string;
            /**
             * Scope
             * @constant
             */
            scope: "ALL_PRIVATE_DATA";
            /**
             * Target Type
             * @constant
             */
            target_type: "ACCOUNT";
        };
        /** AccountDeletionResponse */
        AccountDeletionResponse: {
            /** Completed At */
            completed_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Requested At
             * Format: date-time
             */
            requested_at: string;
            /**
             * Scope
             * @constant
             */
            scope: "ALL_PRIVATE_DATA";
            /**
             * Status
             * @enum {string}
             */
            status: "AWAITING_CONFIRMATION" | "RUNNING" | "PARTIALLY_COMPLETED" | "FAILED_RETRYABLE" | "COMPLETED" | "EXPIRED";
            /**
             * Target Type
             * @constant
             */
            target_type: "ACCOUNT";
            /** Targets */
            targets?: components["schemas"]["AccountDeletionTargetResponse"][];
        };
        /** AccountDeletionRetryRequest */
        AccountDeletionRetryRequest: {
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** AccountDeletionTargetResponse */
        AccountDeletionTargetResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Status
             * @enum {string}
             */
            status: "QUEUED" | "DISPATCHED" | "ACKNOWLEDGED" | "FAILED_RETRYABLE";
            /**
             * Store
             * @enum {string}
             */
            store: "POSTGRESQL" | "NEO4J" | "VECTOR" | "CACHE" | "CHECKPOINT";
        };
        /** ActivityCreateRequest */
        ActivityCreateRequest: {
            /** Activity Type */
            activity_type?: string | null;
            organization?: components["schemas"]["FieldValue"];
            /** Original Narrative */
            original_narrative?: string | null;
            outcome?: components["schemas"]["OutcomeValue"];
            period?: components["schemas"]["PeriodValue"];
            role?: components["schemas"]["FieldValue"];
            /** Title */
            title: string;
            /**
             * Usage Enabled
             * @default true
             */
            usage_enabled: boolean;
        };
        /** ActivityListItemResponse */
        ActivityListItemResponse: {
            /** Activity Type */
            activity_type: string | null;
            /** Current Version */
            current_version: number;
            /** Episode Count */
            episode_count: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Organization Display */
            organization_display: string | null;
            /** Period Display */
            period_display: string | null;
            /**
             * Registration Status
             * @enum {string}
             */
            registration_status: "DRAFT" | "COMPLETED";
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Usage Enabled */
            usage_enabled: boolean;
        };
        /** ActivityMutationResponse */
        ActivityMutationResponse: {
            /** Activity Type */
            activity_type: string | null;
            /**
             * Affected Project Count
             * @default 0
             */
            affected_project_count: number;
            /** Changed Fields */
            changed_fields?: string[];
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Current Version */
            current_version: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            organization: components["schemas"]["FieldValue"];
            /** Original Narrative */
            original_narrative: string | null;
            outcome: components["schemas"]["OutcomeValue"];
            period: components["schemas"]["PeriodValue"];
            /**
             * Reanalyze Available
             * @default false
             */
            reanalyze_available: boolean;
            /**
             * Registration Status
             * @enum {string}
             */
            registration_status: "DRAFT" | "COMPLETED";
            role: components["schemas"]["FieldValue"];
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Usage Enabled */
            usage_enabled: boolean;
        };
        /** ActivityResponse */
        ActivityResponse: {
            /** Activity Type */
            activity_type: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Current Version */
            current_version: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            organization: components["schemas"]["FieldValue"];
            /** Original Narrative */
            original_narrative: string | null;
            outcome: components["schemas"]["OutcomeValue"];
            period: components["schemas"]["PeriodValue"];
            /**
             * Registration Status
             * @enum {string}
             */
            registration_status: "DRAFT" | "COMPLETED";
            role: components["schemas"]["FieldValue"];
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Usage Enabled */
            usage_enabled: boolean;
        };
        /** ActivityUpdateRequest */
        ActivityUpdateRequest: {
            /** Activity Type */
            activity_type?: string | null;
            /** Change Reason */
            change_reason: string;
            organization?: components["schemas"]["FieldValue"] | null;
            /** Original Narrative */
            original_narrative?: string | null;
            outcome?: components["schemas"]["OutcomeValue"] | null;
            period?: components["schemas"]["PeriodValue"] | null;
            role?: components["schemas"]["FieldValue"] | null;
            /** Title */
            title?: string | null;
            /** Usage Enabled */
            usage_enabled?: boolean | null;
        };
        /** AnalyticsConsentUpdateRequest */
        AnalyticsConsentUpdateRequest: {
            /** Opted In */
            opted_in: boolean;
            /** Policy Version */
            policy_version: string;
        };
        /** ApiErrorBody */
        ApiErrorBody: {
            /** Actions */
            actions?: string[];
            /** Code */
            code: string;
            /** Correlation Id */
            correlation_id: string;
            /** Fields */
            fields?: components["schemas"]["ErrorFieldResponse"][];
            /** Message Ko */
            message_ko: string;
            /**
             * Retryable
             * @default false
             */
            retryable: boolean;
        };
        /** ApiErrorResponse */
        ApiErrorResponse: {
            error: components["schemas"]["ApiErrorBody"];
        };
        /** CandidateSelectionRequest */
        CandidateSelectionRequest: {
            /**
             * Question Id
             * Format: uuid
             */
            question_id: string;
            /**
             * Recommendation Run Id
             * Format: uuid
             */
            recommendation_run_id: string;
            /**
             * Replace Existing
             * @default false
             */
            replace_existing: boolean;
            /** Result Version */
            result_version: string;
        };
        /**
         * CompanyResponse
         * @description A read-only entry from the already identified Company catalog.
         */
        CompanyResponse: {
            /** Display Name */
            display_name: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Identification Status */
            identification_status: string;
            /** Official Domain */
            official_domain: string | null;
        };
        /** CompleteRequest */
        CompleteRequest: {
            /** Sensitive Content Confirmation */
            sensitive_content_confirmation?: null;
        };
        /** ConsentResponse */
        ConsentResponse: {
            /** Decided At */
            decided_at: string | null;
            /** Opted In */
            opted_in: boolean;
            /** Policy Version */
            policy_version: string | null;
            /**
             * Type
             * @constant
             */
            type: "ANALYTICS";
        };
        /** CursorListResponse[ActivityListItemResponse] */
        CursorListResponse_ActivityListItemResponse_: {
            /** Items */
            items?: components["schemas"]["ActivityListItemResponse"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** CursorListResponse[CompanyResponse] */
        CursorListResponse_CompanyResponse_: {
            /** Items */
            items?: components["schemas"]["CompanyResponse"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** CursorListResponse[DuplicateSuggestionResponse] */
        CursorListResponse_DuplicateSuggestionResponse_: {
            /** Items */
            items?: components["schemas"]["DuplicateSuggestionResponse"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** CursorListResponse[ExperienceExclusionResponse] */
        CursorListResponse_ExperienceExclusionResponse_: {
            /** Items */
            items?: components["schemas"]["ExperienceExclusionResponse"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** CursorListResponse[InferenceSuggestionResponse] */
        CursorListResponse_InferenceSuggestionResponse_: {
            /** Items */
            items?: components["schemas"]["InferenceSuggestionResponse"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** CursorListResponse[JobListItemResponse] */
        CursorListResponse_JobListItemResponse_: {
            /** Items */
            items?: components["schemas"]["JobListItemResponse"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** CursorListResponse[JobPostingListItemResponse] */
        CursorListResponse_JobPostingListItemResponse_: {
            /** Items */
            items?: components["schemas"]["JobPostingListItemResponse"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** CursorListResponse[NotificationResponse] */
        CursorListResponse_NotificationResponse_: {
            /** Items */
            items?: components["schemas"]["NotificationResponse"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** CursorListResponse[ProjectListItemResponse] */
        CursorListResponse_ProjectListItemResponse_: {
            /** Items */
            items?: components["schemas"]["ProjectListItemResponse"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** CursorListResponse[RecommendationCandidateResponse] */
        CursorListResponse_RecommendationCandidateResponse_: {
            /** Items */
            items?: components["schemas"]["RecommendationCandidateResponse"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** CursorListResponse[ResumeItemResponse] */
        CursorListResponse_ResumeItemResponse_: {
            /** Items */
            items?: components["schemas"]["ResumeItemResponse"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /**
         * DuplicateDecisionRequest
         * @description Merge is intentionally absent: MVP has no public merge execution path.
         */
        DuplicateDecisionRequest: {
            /**
             * Decision
             * @enum {string}
             */
            decision: "KEEP_SEPARATE" | "DISMISSED";
            /** Reason */
            reason?: string | null;
        };
        /** DuplicateDecisionResponse */
        DuplicateDecisionResponse: {
            /**
             * Decided At
             * Format: date-time
             */
            decided_at: string;
            /**
             * Decision
             * @enum {string}
             */
            decision: "KEEP_SEPARATE" | "DISMISSED";
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Reason */
            reason: string | null;
        };
        /** DuplicateSuggestionResponse */
        DuplicateSuggestionResponse: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            latest_decision: components["schemas"]["DuplicateDecisionResponse"] | null;
            /**
             * Left Episode Id
             * Format: uuid
             */
            left_episode_id: string;
            /**
             * Left Episode Version Id
             * Format: uuid
             */
            left_episode_version_id: string;
            /** Reason */
            reason: string;
            /**
             * Right Episode Id
             * Format: uuid
             */
            right_episode_id: string;
            /**
             * Right Episode Version Id
             * Format: uuid
             */
            right_episode_version_id: string;
            /**
             * Status
             * @enum {string}
             */
            status: "PENDING" | "DECIDED" | "SUPERSEDED";
        };
        /** EpisodeCreateRequest */
        EpisodeCreateRequest: {
            actions?: components["schemas"]["FieldValue"];
            decision_reasons?: components["schemas"]["FieldValue"];
            decisions?: components["schemas"]["FieldValue"];
            goal?: components["schemas"]["FieldValue"];
            learning?: components["schemas"]["FieldValue"];
            /** Original Narrative */
            original_narrative?: string | null;
            problem?: components["schemas"]["FieldValue"];
            result?: components["schemas"]["FieldValue"];
            situation?: components["schemas"]["FieldValue"];
            /** Technologies */
            technologies?: string[];
            /** Title */
            title: string;
            /**
             * Usage Enabled
             * @default true
             */
            usage_enabled: boolean;
        };
        /** EpisodeListItemResponse */
        EpisodeListItemResponse: {
            /**
             * Activity Id
             * Format: uuid
             */
            activity_id: string;
            /** Current Version */
            current_version: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Registration Status
             * @enum {string}
             */
            registration_status: "DRAFT" | "COMPLETED";
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Usage Enabled */
            usage_enabled: boolean;
        };
        /** EpisodeMutationResponse */
        EpisodeMutationResponse: {
            actions: components["schemas"]["FieldValue"];
            /**
             * Activity Id
             * Format: uuid
             */
            activity_id: string;
            /** Changed Fields */
            changed_fields?: string[];
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Current Version */
            current_version: number;
            decision_reasons: components["schemas"]["FieldValue"];
            decisions: components["schemas"]["FieldValue"];
            goal: components["schemas"]["FieldValue"];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            learning: components["schemas"]["FieldValue"];
            /** Original Narrative */
            original_narrative: string | null;
            problem: components["schemas"]["FieldValue"];
            /**
             * Registration Status
             * @enum {string}
             */
            registration_status: "DRAFT" | "COMPLETED";
            result: components["schemas"]["FieldValue"];
            situation: components["schemas"]["FieldValue"];
            /** Technologies */
            technologies?: string[];
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Usage Enabled */
            usage_enabled: boolean;
        };
        /** EpisodeReference */
        EpisodeReference: {
            /**
             * Episode Id
             * Format: uuid
             */
            episode_id: string;
            /** Version */
            version: number;
        };
        /** EpisodeResponse */
        EpisodeResponse: {
            actions: components["schemas"]["FieldValue"];
            /**
             * Activity Id
             * Format: uuid
             */
            activity_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Current Version */
            current_version: number;
            decision_reasons: components["schemas"]["FieldValue"];
            decisions: components["schemas"]["FieldValue"];
            goal: components["schemas"]["FieldValue"];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            learning: components["schemas"]["FieldValue"];
            /** Original Narrative */
            original_narrative: string | null;
            problem: components["schemas"]["FieldValue"];
            /**
             * Registration Status
             * @enum {string}
             */
            registration_status: "DRAFT" | "COMPLETED";
            result: components["schemas"]["FieldValue"];
            situation: components["schemas"]["FieldValue"];
            /** Technologies */
            technologies?: string[];
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Usage Enabled */
            usage_enabled: boolean;
        };
        /** EpisodeUpdateRequest */
        EpisodeUpdateRequest: {
            actions?: components["schemas"]["FieldValue"] | null;
            /** Change Reason */
            change_reason: string;
            decision_reasons?: components["schemas"]["FieldValue"] | null;
            decisions?: components["schemas"]["FieldValue"] | null;
            goal?: components["schemas"]["FieldValue"] | null;
            learning?: components["schemas"]["FieldValue"] | null;
            /** Original Narrative */
            original_narrative?: string | null;
            problem?: components["schemas"]["FieldValue"] | null;
            result?: components["schemas"]["FieldValue"] | null;
            situation?: components["schemas"]["FieldValue"] | null;
            /** Technologies */
            technologies?: string[] | null;
            /** Title */
            title?: string | null;
            /** Usage Enabled */
            usage_enabled?: boolean | null;
        };
        /** ErrorFieldResponse */
        ErrorFieldResponse: {
            /** Field */
            field: string;
            /** Reason */
            reason: string;
        };
        /** ExperienceExclusionCreateRequest */
        ExperienceExclusionCreateRequest: {
            /** Activity Id */
            activity_id?: string | null;
            /** Company Id */
            company_id?: string | null;
            /** Episode Id */
            episode_id?: string | null;
            /** Project Id */
            project_id?: string | null;
            /** Reason */
            reason?: string | null;
            /** Role Id */
            role_id?: string | null;
            /**
             * Scope
             * @enum {string}
             */
            scope: "GLOBAL" | "COMPANY" | "ROLE" | "PROJECT";
        };
        /** ExperienceExclusionResponse */
        ExperienceExclusionResponse: {
            /** Activity Id */
            activity_id: string | null;
            /** Company Id */
            company_id: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Episode Id */
            episode_id: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Project Id */
            project_id: string | null;
            /** Reason */
            reason: string | null;
            /** Revoked At */
            revoked_at: string | null;
            /** Role Id */
            role_id: string | null;
            /**
             * Scope
             * @enum {string}
             */
            scope: "GLOBAL" | "COMPANY" | "ROLE" | "PROJECT";
        };
        /** FeedbackCreateRequest */
        FeedbackCreateRequest: {
            /** Category L1 */
            category_l1: string;
            /** Category L2 */
            category_l2?: string | null;
            /** Decision Helpfulness */
            decision_helpfulness?: ("HELPFUL" | "NOT_HELPFUL" | "NOT_SURE") | null;
            /** Other Text */
            other_text?: string | null;
        };
        /** FeedbackResponse */
        FeedbackResponse: {
            /** Category L1 */
            category_l1: string;
            /** Category L2 */
            category_l2: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Decision Helpfulness */
            decision_helpfulness: ("HELPFUL" | "NOT_HELPFUL" | "NOT_SURE") | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Missing Activity Id */
            missing_activity_id: string | null;
            /** Missing Episode Id */
            missing_episode_id: string | null;
            /** Run Id */
            run_id: string | null;
        };
        /**
         * FieldAvailability
         * @enum {string}
         */
        FieldAvailability: "PROVIDED" | "SKIPPED" | "NOT_APPLICABLE" | "NOT_REMEMBERED" | "NOT_PROVIDED";
        /**
         * FieldValue
         * @description A user value together with an explicit reason when it is unavailable.
         */
        FieldValue: {
            /** @default NOT_PROVIDED */
            availability: components["schemas"]["FieldAvailability"];
            /** Note */
            note?: null;
            /** Value */
            value?: string | null;
        };
        /** HomeExperienceStoreSummary */
        HomeExperienceStoreSummary: {
            /** Activity Count */
            activity_count: number;
            /** Draft Count */
            draft_count: number;
        };
        /** HomeNotificationSummary */
        HomeNotificationSummary: {
            /** Critical Count */
            critical_count: number;
            /** Unread Count */
            unread_count: number;
        };
        /** HomeProjectSummary */
        HomeProjectSummary: {
            /** In Progress Count */
            in_progress_count: number;
            /** Needs Review Count */
            needs_review_count: number;
            /** Recent */
            recent?: unknown[];
        };
        /** HomeResponse */
        HomeResponse: {
            experience_store: components["schemas"]["HomeExperienceStoreSummary"];
            notifications: components["schemas"]["HomeNotificationSummary"];
            projects: components["schemas"]["HomeProjectSummary"];
            /** Resume Items */
            resume_items?: components["schemas"]["ResumeItemResponse"][];
            /** Running Job Count */
            running_job_count: number;
        };
        /** InferenceDecisionRequest */
        InferenceDecisionRequest: {
            /**
             * Decision
             * @enum {string}
             */
            decision: "APPROVED" | "MODIFIED" | "REJECTED";
            /** Modified Value */
            modified_value?: {
                [key: string]: unknown;
            } | null;
            /** Reason */
            reason?: string | null;
        };
        /** InferenceDecisionResponse */
        InferenceDecisionResponse: {
            /**
             * Decided At
             * Format: date-time
             */
            decided_at: string;
            /**
             * Decision
             * @enum {string}
             */
            decision: "APPROVED" | "MODIFIED" | "REJECTED";
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Modified Value */
            modified_value: {
                [key: string]: unknown;
            } | null;
            /** Reason */
            reason: string | null;
        };
        /**
         * InferenceJobStartRequest
         * @description Acceptance shape reserved for the future inference worker boundary.
         */
        InferenceJobStartRequest: {
            /** Episode Version */
            episode_version: number;
            /** Inference Types */
            inference_types: string[];
            /**
             * Replace Pending
             * @default false
             */
            replace_pending: boolean;
        };
        /**
         * InferenceSourceReferenceResponse
         * @description Reference metadata only; never the underlying source text.
         */
        InferenceSourceReferenceResponse: {
            /** Field Name */
            field_name: string | null;
            /**
             * Reference Type
             * @enum {string}
             */
            reference_type: "EPISODE_VERSION" | "SOURCE_VERSION" | "EVIDENCE_SPAN";
            /** Source Span End */
            source_span_end: number | null;
            /** Source Span Start */
            source_span_start: number | null;
        };
        /** InferenceSuggestionResponse */
        InferenceSuggestionResponse: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Episode Id
             * Format: uuid
             */
            episode_id: string;
            /**
             * Episode Version Id
             * Format: uuid
             */
            episode_version_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            latest_decision: components["schemas"]["InferenceDecisionResponse"] | null;
            /** Proposed Value */
            proposed_value: {
                [key: string]: unknown;
            };
            /** Sources */
            sources?: components["schemas"]["InferenceSourceReferenceResponse"][];
            /**
             * Status
             * @enum {string}
             */
            status: "PENDING" | "DECIDED" | "SUPERSEDED";
            /** Suggestion Type */
            suggestion_type: string;
        };
        /** JobActionRequest */
        JobActionRequest: {
            /**
             * Acknowledge Rate Limit
             * @default false
             */
            acknowledge_rate_limit: boolean;
            /**
             * Action
             * @enum {string}
             */
            action: "RETRY" | "CONTINUE_LIMITED" | "STOP";
            /** Expected Input Version */
            expected_input_version: string | null;
            /** Expected Result Version */
            expected_result_version: string | null;
            /**
             * Required Action Id
             * Format: uuid
             */
            required_action_id: string;
        };
        /** JobCancelRequest */
        JobCancelRequest: Record<string, never>;
        /** JobCheckpointResponse */
        JobCheckpointResponse: {
            /** Analysis Input Version */
            analysis_input_version?: string | null;
            /** Available */
            available: boolean;
            /** Last Completed Stage */
            last_completed_stage?: string | null;
        };
        /** JobFailureResponse */
        JobFailureResponse: {
            /** Code */
            code: string | null;
            /** Message */
            message: string | null;
            /** Retry After Seconds */
            retry_after_seconds: number | null;
            /** Retryable */
            retryable: boolean;
        };
        /** JobInputRefResponse */
        JobInputRefResponse: {
            /** Id */
            id: string;
            /** Type */
            type: string;
            /** Version */
            version: number | string;
        };
        /** JobListItemResponse */
        JobListItemResponse: {
            /**
             * Completeness
             * @enum {string}
             */
            completeness: "none" | "partial" | "complete";
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Dispatch Status
             * @enum {string}
             */
            dispatch_status: "OUTBOX_PENDING" | "ENQUEUED" | "CLAIMED" | "BLOCKED" | "INVALIDATED";
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Job Type */
            job_type: string;
            progress: components["schemas"]["JobProgressResponse"];
            /** Required Action Count */
            required_action_count: number;
            /** Stage */
            stage: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "QUEUED" | "RUNNING" | "WAITING_USER" | "PAUSED_RATE_LIMIT" | "SUCCEEDED" | "FAILED_RETRYABLE" | "FAILED_FINAL" | "CANCEL_REQUESTED" | "CANCELLED";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** JobPostingListItemResponse */
        JobPostingListItemResponse: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Published At */
            published_at: string | null;
            /** Role Display */
            role_display: string | null;
            /** Source Version Ref */
            source_version_ref: string;
            /** Status */
            status: string;
            /** Title */
            title: string;
        };
        /** JobProgressResponse */
        JobProgressResponse: {
            /** Completed Units */
            completed_units: number;
            /** Percent */
            percent: number | null;
            /** Total Units */
            total_units: number | null;
        };
        /** JobRequiredActionResponse */
        JobRequiredActionResponse: {
            /**
             * Code
             * @enum {string}
             */
            code: "RETRY" | "CONTINUE_LIMITED" | "STOP";
            /** Context Code */
            context_code: string | null;
            /** Expected Input Version */
            expected_input_version: string | null;
            /** Expected Result Version */
            expected_result_version: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Status
             * @constant
             */
            status: "OPEN";
        };
        /** JobResponse */
        JobResponse: {
            checkpoint: components["schemas"]["JobCheckpointResponse"];
            /**
             * Completeness
             * @enum {string}
             */
            completeness: "none" | "partial" | "complete";
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Dispatch Status
             * @enum {string}
             */
            dispatch_status: "OUTBOX_PENDING" | "ENQUEUED" | "CLAIMED" | "BLOCKED" | "INVALIDATED";
            failure: components["schemas"]["JobFailureResponse"];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Input Refs */
            input_refs?: components["schemas"]["JobInputRefResponse"][];
            /** Job Type */
            job_type: string;
            /** Limitations */
            limitations?: string[];
            progress: components["schemas"]["JobProgressResponse"];
            /** Required Actions */
            required_actions?: components["schemas"]["JobRequiredActionResponse"][];
            /** Stage */
            stage: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "QUEUED" | "RUNNING" | "WAITING_USER" | "PAUSED_RATE_LIMIT" | "SUCCEEDED" | "FAILED_RETRYABLE" | "FAILED_FINAL" | "CANCEL_REQUESTED" | "CANCELLED";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** JobRetryRequest */
        JobRetryRequest: {
            /**
             * Acknowledge Rate Limit
             * @default false
             */
            acknowledge_rate_limit: boolean;
            /** Expected Input Version */
            expected_input_version: string | null;
            /** Expected Result Version */
            expected_result_version: string | null;
            /** From Stage */
            from_stage: string;
            /**
             * Required Action Id
             * Format: uuid
             */
            required_action_id: string;
        };
        /**
         * MaterialSelectionResponse
         * @description Current single-candidate selection exposed by the synthetic M1 flow.
         */
        MaterialSelectionResponse: {
            /**
             * Candidate Id
             * Format: uuid
             */
            candidate_id: string;
            episode_ref: components["schemas"]["EpisodeReference"];
            /**
             * Project Id
             * Format: uuid
             */
            project_id: string;
            /**
             * Question Id
             * Format: uuid
             */
            question_id: string;
            /**
             * Recommendation Run Id
             * Format: uuid
             */
            recommendation_run_id: string;
            /**
             * Selected At
             * Format: date-time
             */
            selected_at: string;
            /**
             * Selection Id
             * Format: uuid
             */
            selection_id: string;
            /** Warnings */
            warnings?: components["schemas"]["SelectionWarning"][];
        };
        /** MissingCandidateFeedbackCreateRequest */
        MissingCandidateFeedbackCreateRequest: {
            /** Category L2 */
            category_l2?: string | null;
            /**
             * Episode Id
             * Format: uuid
             */
            episode_id: string;
            /** Other Text */
            other_text?: string | null;
        };
        /** NotificationResponse */
        NotificationResponse: {
            /** Action Url */
            action_url: string | null;
            /** Archived */
            archived: boolean;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Message */
            message: string;
            /** Project Id */
            project_id: string | null;
            /** Read */
            read: boolean;
            /**
             * Severity
             * @enum {string}
             */
            severity: "INFO" | "WARNING" | "CRITICAL";
            /** Title */
            title: string;
            /** Type */
            type: string;
        };
        /** NotificationUpdateRequest */
        NotificationUpdateRequest: {
            /** Archived */
            archived?: boolean | null;
            /** Read */
            read?: boolean | null;
        };
        /** OutcomeValue */
        OutcomeValue: {
            /** Status */
            status?: string | null;
            summary?: components["schemas"]["FieldValue"];
        };
        /** PeriodValue */
        PeriodValue: {
            /** @default NOT_PROVIDED */
            availability: components["schemas"]["FieldAvailability"];
            /** End Date */
            end_date?: string | null;
            /** Precision */
            precision?: string | null;
            /** Start Date */
            start_date?: string | null;
        };
        /** ProjectCreateRequest */
        ProjectCreateRequest: {
            /**
             * Company Id
             * Format: uuid
             */
            company_id: string;
            /** Job Posting Id */
            job_posting_id?: string | null;
            /** Official Job Posting Url */
            official_job_posting_url?: null;
            /** Organization Name */
            organization_name?: string | null;
            /** Role Name */
            role_name: string;
            /** Season */
            season?: string | null;
            /** Title */
            title: string;
        };
        /** ProjectJobPostingLinkRequest */
        ProjectJobPostingLinkRequest: {
            /** Job Posting Id */
            job_posting_id?: string | null;
            /**
             * Mode
             * @enum {string}
             */
            mode: "EXISTING" | "OFFICIAL_URL";
            /** Official Url */
            official_url?: string | null;
        };
        /** ProjectListItemResponse */
        ProjectListItemResponse: {
            /**
             * Company Id
             * Format: uuid
             */
            company_id: string;
            /** Current Step */
            current_step: string | null;
            /** Current Version */
            current_version: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Status
             * @enum {string}
             */
            status: "DRAFT" | "IN_PROGRESS" | "PAUSED" | "MATERIALS_SELECTED" | "NEEDS_REVIEW" | "ARCHIVED";
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** ProjectMutationResponse */
        ProjectMutationResponse: {
            /** Changed Fields */
            changed_fields?: string[];
            /**
             * Company Id
             * Format: uuid
             */
            company_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Current Step */
            current_step: string | null;
            /** Current Version */
            current_version: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Job Posting Id */
            job_posting_id: string | null;
            /** Organization Name */
            organization_name: string | null;
            /** Role Name */
            role_name: string;
            /** Season */
            season: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "DRAFT" | "IN_PROGRESS" | "PAUSED" | "MATERIALS_SELECTED" | "NEEDS_REVIEW" | "ARCHIVED";
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** ProjectResponse */
        ProjectResponse: {
            /**
             * Company Id
             * Format: uuid
             */
            company_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Current Step */
            current_step: string | null;
            /** Current Version */
            current_version: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Job Posting Id */
            job_posting_id: string | null;
            /** Organization Name */
            organization_name: string | null;
            /** Role Name */
            role_name: string;
            /** Season */
            season: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "DRAFT" | "IN_PROGRESS" | "PAUSED" | "MATERIALS_SELECTED" | "NEEDS_REVIEW" | "ARCHIVED";
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** ProjectUpdateRequest */
        ProjectUpdateRequest: {
            /** Change Reason */
            change_reason: string;
            /** Company Id */
            company_id?: string | null;
            /** Job Posting Id */
            job_posting_id?: string | null;
            /** Official Job Posting Url */
            official_job_posting_url?: null;
            /** Organization Name */
            organization_name?: string | null;
            /** Role Name */
            role_name?: string | null;
            /** Season */
            season?: string | null;
            /** Title */
            title?: string | null;
        };
        /** ProjectVersionSummaryResponse */
        ProjectVersionSummaryResponse: {
            /** Change Reason */
            change_reason: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Is Current */
            is_current: boolean;
            /** Version */
            version: number;
        };
        /** QuestionCreateRequest */
        QuestionCreateRequest: {
            /** Character Limit */
            character_limit?: number | null;
            /** Display Order */
            display_order: number;
            /** Prompt */
            prompt: string;
            /**
             * Source
             * @default USER_INPUT
             * @enum {string}
             */
            source: "USER_INPUT" | "OFFICIAL_POSTING";
        };
        /** QuestionListItemResponse */
        QuestionListItemResponse: {
            /** Character Limit */
            character_limit: number | null;
            /** Current Version */
            current_version: number;
            /** Display Order */
            display_order: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Prompt */
            prompt: string;
            /**
             * Source
             * @enum {string}
             */
            source: "USER_INPUT" | "OFFICIAL_POSTING";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** QuestionMutationResponse */
        QuestionMutationResponse: {
            /** Changed Fields */
            changed_fields?: string[];
            /** Character Limit */
            character_limit: number | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Current Version */
            current_version: number;
            /** Display Order */
            display_order: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Project Id
             * Format: uuid
             */
            project_id: string;
            /** Prompt */
            prompt: string;
            /**
             * Source
             * @enum {string}
             */
            source: "USER_INPUT" | "OFFICIAL_POSTING";
            /**
             * Status
             * @enum {string}
             */
            status: "ACTIVE" | "ARCHIVED";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** QuestionResponse */
        QuestionResponse: {
            /** Character Limit */
            character_limit: number | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Current Version */
            current_version: number;
            /** Display Order */
            display_order: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Project Id
             * Format: uuid
             */
            project_id: string;
            /** Prompt */
            prompt: string;
            /**
             * Source
             * @enum {string}
             */
            source: "USER_INPUT" | "OFFICIAL_POSTING";
            /**
             * Status
             * @enum {string}
             */
            status: "ACTIVE" | "ARCHIVED";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** QuestionUpdateRequest */
        QuestionUpdateRequest: {
            /** Character Limit */
            character_limit?: number | null;
            /** Prompt */
            prompt?: string | null;
            /** Source */
            source?: ("USER_INPUT" | "OFFICIAL_POSTING") | null;
        };
        /** QuestionVersionSummaryResponse */
        QuestionVersionSummaryResponse: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Is Current */
            is_current: boolean;
            /** Version */
            version: number;
        };
        /**
         * RecommendationCandidateResponse
         * @description The W4-independent, persisted Candidate summary surface only.
         */
        RecommendationCandidateResponse: {
            /** Candidate No */
            candidate_no: number;
            /**
             * Episode Version Id
             * Format: uuid
             */
            episode_version_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Limitation Summary */
            limitation_summary: string | null;
            /**
             * Match Status
             * @enum {string}
             */
            match_status: "DIRECT_MATCH" | "PARTIAL_RELEVANCE" | "NEEDS_VERIFICATION" | "NO_RELEVANT_EVIDENCE";
            /** Result Version */
            result_version: string;
            /** Short Reason */
            short_reason: string;
            /** Strength Summary */
            strength_summary: string | null;
            /**
             * Validation Status
             * @enum {string}
             */
            validation_status: "PENDING" | "PASSED" | "LIMITED" | "FAILED";
        };
        /** RecommendationPreferenceResponse */
        RecommendationPreferenceResponse: {
            /** Default Candidate Limit */
            default_candidate_limit: number;
            /** Evidence Display Mode */
            evidence_display_mode: string;
            /** Question Display Mode */
            question_display_mode: string;
            /** Show Information Completeness */
            show_information_completeness: boolean;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Version */
            version: number;
        };
        /** RecommendationPreferenceUpdateRequest */
        RecommendationPreferenceUpdateRequest: {
            /** Default Candidate Limit */
            default_candidate_limit?: number | null;
            /** Evidence Display Mode */
            evidence_display_mode?: string | null;
            /** Question Display Mode */
            question_display_mode?: string | null;
            /** Show Information Completeness */
            show_information_completeness?: boolean | null;
        };
        /**
         * RecommendationRunCreateRequest
         * @description Version fences for freezing a new synthetic recommendation input.
         */
        RecommendationRunCreateRequest: {
            /**
             * Allow Limited Analysis
             * @default true
             */
            allow_limited_analysis: boolean;
            /**
             * Candidate Limit
             * @default 5
             */
            candidate_limit: number;
            /**
             * Include Excluded
             * @default false
             */
            include_excluded: boolean;
            /** Question Version */
            question_version: number;
            /** Snapshot Version */
            snapshot_version: number;
        };
        /** RecommendationRunResponse */
        RecommendationRunResponse: {
            /** Candidates Url */
            candidates_url: string;
            /** Completed At */
            completed_at: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Limitations */
            limitations?: string[];
            /** Limited Analysis */
            limited_analysis: boolean;
            /**
             * Project Id
             * Format: uuid
             */
            project_id: string;
            /**
             * Question Id
             * Format: uuid
             */
            question_id: string;
            /** Question Version */
            question_version: number;
            /** Requested Candidate Limit */
            requested_candidate_limit: number;
            /**
             * Result Origin
             * @enum {string}
             */
            result_origin: "SYNTHETIC" | "ENGINE";
            /**
             * Result Status
             * @enum {string}
             */
            result_status: "PENDING" | "READY" | "LIMITED" | "FAILED";
            /**
             * Snapshot Id
             * Format: uuid
             */
            snapshot_id: string;
            /** Snapshot Version */
            snapshot_version: number;
            /**
             * Status
             * @enum {string}
             */
            status: "PENDING" | "RUNNING" | "SUCCEEDED" | "LIMITED" | "FAILED" | "CANCELLED";
        };
        /** ResumeItemResponse */
        ResumeItemResponse: {
            /** Blocking Reason */
            blocking_reason: string | null;
            /** Current Step */
            current_step: string | null;
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            /**
             * Resource Type
             * @enum {string}
             */
            resource_type: "ACTIVITY_DRAFT" | "PROJECT" | "WAITING_USER_JOB";
            /** Resume Url */
            resume_url: string;
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** RetentionPolicyStatusResponse */
        RetentionPolicyStatusResponse: {
            /**
             * Status
             * @constant
             */
            status: "PENDING_CONFIGURATION";
        };
        /** RunFeedbackCreateRequest */
        RunFeedbackCreateRequest: {
            /** Category L1 */
            category_l1: string;
            /** Category L2 */
            category_l2?: string | null;
            /** Decision Helpfulness */
            decision_helpfulness?: ("HELPFUL" | "NOT_HELPFUL" | "NOT_SURE") | null;
            /** Other Text */
            other_text?: string | null;
        };
        /** SelectionWarning */
        SelectionWarning: {
            /** Code */
            code: string;
            /** Message */
            message: string;
            /**
             * Severity
             * @constant
             */
            severity: "WARNING";
        };
        /** SourceCollectionAcceptanceResponse */
        SourceCollectionAcceptanceResponse: {
            /**
             * Job Id
             * Format: uuid
             */
            job_id: string;
            /** Replayed */
            replayed: boolean;
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
            /**
             * Status
             * @enum {string}
             */
            status: "QUEUED" | "RUNNING" | "WAITING_USER" | "PAUSED_RATE_LIMIT";
        };
        /** SourceCollectionCreateRequest */
        SourceCollectionCreateRequest: {
            /** Official Url */
            official_url: string;
            /**
             * Purpose
             * @enum {string}
             */
            purpose: "COMPANY_PROFILE" | "JOB_POSTING";
            /**
             * Source Type
             * @constant
             */
            source_type: "OFFICIAL_URL";
        };
        /**
         * SourceCollectionErrorResponse
         * @description Named public error envelope for generated source-collection clients.
         */
        SourceCollectionErrorResponse: {
            error: components["schemas"]["ApiErrorBody"];
        };
        /** SourceCollectionProgressResponse */
        SourceCollectionProgressResponse: {
            /**
             * Job Id
             * Format: uuid
             */
            job_id: string;
            progress: components["schemas"]["JobProgressResponse"];
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
            /** Stage */
            stage: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "QUEUED" | "RUNNING" | "WAITING_USER" | "PAUSED_RATE_LIMIT" | "SUCCEEDED" | "FAILED_RETRYABLE" | "FAILED_FINAL" | "CANCEL_REQUESTED" | "CANCELLED";
        };
        /** UserMeResponse */
        UserMeResponse: {
            /** Account Status */
            account_status: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Display Name */
            display_name: string;
            /** Email */
            email: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
        };
        /** UserSettingsResponse */
        UserSettingsResponse: {
            /** Display Options */
            display_options: {
                [key: string]: unknown;
            };
            /** Locale */
            locale: string;
            /** Timezone */
            timezone: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Version */
            version: number;
        };
        /** UserSettingsUpdateRequest */
        UserSettingsUpdateRequest: {
            /** Display Options */
            display_options?: {
                [key: string]: unknown;
            } | null;
            /** Locale */
            locale?: string | null;
            /** Timezone */
            timezone?: string | null;
        };
        /** VersionSummaryResponse */
        VersionSummaryResponse: {
            /** Change Reason */
            change_reason: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Created By */
            created_by: string;
            /** Is Current */
            is_current: boolean;
            /** Version */
            version: number;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    create_account_deletion_preview_api_v1_account_deletion_previews_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AccountDeletionPreviewResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    confirm_account_deletion_api_v1_account_deletion_requests_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AccountDeletionConfirmRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AccountDeletionResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_account_deletion_api_v1_account_deletion_requests__deletion_request_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                deletion_request_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AccountDeletionResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    retry_account_deletion_target_api_v1_account_deletion_requests__deletion_request_id__retry_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                deletion_request_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AccountDeletionRetryRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AccountDeletionResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_activities_api_v1_activities_get: {
        parameters: {
            query?: {
                activity_type?: string[] | null;
                cursor?: string | null;
                limit?: number;
                q?: string | null;
                sort?: "updated_at_desc" | "start_date_desc" | "created_at_desc";
                status?: string | null;
                usage_enabled?: boolean | null;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorListResponse_ActivityListItemResponse_"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    create_activity_api_v1_activities_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ActivityCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActivityResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_activity_api_v1_activities__activity_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                activity_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActivityResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    update_activity_api_v1_activities__activity_id__patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
                "If-Match"?: string | null;
            };
            path: {
                activity_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ActivityUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActivityMutationResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    complete_activity_api_v1_activities__activity_id__complete_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
                "If-Match"?: string | null;
            };
            path: {
                activity_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CompleteRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActivityMutationResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_episodes_api_v1_activities__activity_id__episodes_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                activity_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EpisodeListItemResponse"][];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    create_episode_api_v1_activities__activity_id__episodes_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                activity_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EpisodeCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EpisodeResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_activity_versions_api_v1_activities__activity_id__versions_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                activity_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["VersionSummaryResponse"][];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_activity_version_api_v1_activities__activity_id__versions__version_no__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                activity_id: string;
                version_no: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActivityResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_projects_api_v1_application_projects_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorListResponse_ProjectListItemResponse_"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    create_project_api_v1_application_projects_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProjectCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProjectResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_project_api_v1_application_projects__project_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProjectResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    update_project_api_v1_application_projects__project_id__patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
                "If-Match"?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProjectUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProjectMutationResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    link_existing_job_posting_api_v1_application_projects__project_id__job_posting_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
                "If-Match"?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProjectJobPostingLinkRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProjectMutationResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_questions_api_v1_application_projects__project_id__questions_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionListItemResponse"][];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    create_question_api_v1_application_projects__project_id__questions_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QuestionCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_latest_source_collection_api_v1_application_projects__project_id__source_collections_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourceCollectionProgressResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    create_source_collection_api_v1_application_projects__project_id__source_collections_post: {
        parameters: {
            query?: never;
            header: {
                authorization?: string | null;
                "Idempotency-Key": string;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SourceCollectionCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    Location?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourceCollectionAcceptanceResponse"];
                };
            };
            /** @description 지원하지 않거나 안전하지 않은 공식 URL입니다. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourceCollectionErrorResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_source_collection_progress_api_v1_application_projects__project_id__source_collections__job_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                job_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourceCollectionProgressResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_project_versions_api_v1_application_projects__project_id__versions_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProjectVersionSummaryResponse"][];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    complete_google_login_api_v1_auth_google_callback_get: {
        parameters: {
            query: {
                code: string;
                state: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            302: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    start_google_login_api_v1_auth_google_start_get: {
        parameters: {
            query?: {
                return_to?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            302: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    logout_epick_session_api_v1_auth_logout_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    refresh_epick_access_token_api_v1_auth_refresh_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AccessTokenResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_companies_api_v1_companies_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
                query?: string | null;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorListResponse_CompanyResponse_"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_company_api_v1_companies__company_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                company_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CompanyResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_company_job_postings_api_v1_companies__company_id__job_postings_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: {
                authorization?: string | null;
            };
            path: {
                company_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorListResponse_JobPostingListItemResponse_"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_consents_api_v1_consents_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConsentResponse"][];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    update_analytics_consent_api_v1_consents_analytics_patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AnalyticsConsentUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConsentResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_data_retention_api_v1_data_retention_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RetentionPolicyStatusResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    update_data_retention_api_v1_data_retention_patch: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_episode_api_v1_episodes__episode_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                episode_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EpisodeResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    update_episode_api_v1_episodes__episode_id__patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
                "If-Match"?: string | null;
            };
            path: {
                episode_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EpisodeUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EpisodeMutationResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    complete_episode_api_v1_episodes__episode_id__complete_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
                "If-Match"?: string | null;
            };
            path: {
                episode_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CompleteRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EpisodeMutationResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    start_inference_job_api_v1_episodes__episode_id__inference_jobs_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                episode_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InferenceJobStartRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_inferences_api_v1_episodes__episode_id__inferences_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: {
                authorization?: string | null;
            };
            path: {
                episode_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorListResponse_InferenceSuggestionResponse_"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    decide_inference_api_v1_episodes__episode_id__inferences__inference_id__patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                episode_id: string;
                inference_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InferenceDecisionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InferenceSuggestionResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_episode_versions_api_v1_episodes__episode_id__versions_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                episode_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["VersionSummaryResponse"][];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_episode_version_api_v1_episodes__episode_id__versions__version_no__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                episode_id: string;
                version_no: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EpisodeResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_experience_exclusions_api_v1_experience_exclusions_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorListResponse_ExperienceExclusionResponse_"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    create_experience_exclusion_api_v1_experience_exclusions_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExperienceExclusionCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExperienceExclusionResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    revoke_experience_exclusion_api_v1_experience_exclusions__exclusion_id__delete: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                exclusion_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_duplicate_suggestions_api_v1_experiences_duplicates_get: {
        parameters: {
            query?: {
                activity_id?: string | null;
                cursor?: string | null;
                episode_id?: string | null;
                limit?: number;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorListResponse_DuplicateSuggestionResponse_"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    decide_duplicate_suggestion_api_v1_experiences_duplicates__duplicate_id__patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                duplicate_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DuplicateDecisionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DuplicateSuggestionResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    create_feedback_api_v1_feedback_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FeedbackCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FeedbackResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_home_api_v1_home_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HomeResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_jobs_api_v1_jobs_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorListResponse_JobListItemResponse_"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_job_api_v1_jobs__job_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    submit_job_action_api_v1_jobs__job_id__actions_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["JobActionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    cancel_job_api_v1_jobs__job_id__cancel_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["JobCancelRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_job_checkpoint_api_v1_jobs__job_id__checkpoint_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobCheckpointResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    retry_job_api_v1_jobs__job_id__retry_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["JobRetryRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_notifications_api_v1_notifications_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
                severity?: string | null;
                type?: string | null;
                unread_only?: boolean;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorListResponse_NotificationResponse_"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    update_notification_api_v1_notifications__notification_id__patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                notification_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NotificationUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["NotificationResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_question_api_v1_questions__question_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                question_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    archive_question_api_v1_questions__question_id__delete: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
                "If-Match"?: string | null;
            };
            path: {
                question_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    update_question_api_v1_questions__question_id__patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
                "If-Match"?: string | null;
            };
            path: {
                question_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QuestionUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionMutationResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    create_recommendation_run_api_v1_questions__question_id__recommendation_runs_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                question_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RecommendationRunCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecommendationRunResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_current_material_selection_api_v1_questions__question_id__selection_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                question_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MaterialSelectionResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    clear_current_material_selection_api_v1_questions__question_id__selection_delete: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                question_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_question_versions_api_v1_questions__question_id__versions_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                question_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionVersionSummaryResponse"][];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_recommendation_candidate_api_v1_recommendation_candidates__candidate_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                candidate_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecommendationCandidateResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    select_recommendation_candidate_api_v1_recommendation_candidates__candidate_id__select_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                candidate_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CandidateSelectionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MaterialSelectionResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_recommendation_preferences_api_v1_recommendation_preferences_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecommendationPreferenceResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    update_recommendation_preferences_api_v1_recommendation_preferences_patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
                "If-Match"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RecommendationPreferenceUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecommendationPreferenceResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_recommendation_run_api_v1_recommendation_runs__run_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecommendationRunResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_recommendation_candidates_api_v1_recommendation_runs__run_id__candidates_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: {
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorListResponse_RecommendationCandidateResponse_"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    create_run_feedback_api_v1_recommendation_runs__run_id__feedback_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RunFeedbackCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FeedbackResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    create_missing_candidate_feedback_api_v1_recommendation_runs__run_id__missing_candidate_feedback_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MissingCandidateFeedbackCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FeedbackResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    list_resume_items_api_v1_resume_items_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
                type?: ("ACTIVITY_DRAFT" | "PROJECT" | "WAITING_USER_JOB") | null;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorListResponse_ResumeItemResponse_"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_settings_api_v1_settings_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserSettingsResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    update_settings_api_v1_settings_patch: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "Idempotency-Key"?: string | null;
                "If-Match"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UserSettingsUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserSettingsResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    get_current_user_api_v1_users_me_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserMeResponse"];
                };
            };
            /** @description 인증 정보가 없거나 유효하지 않습니다. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청한 리소스를 찾을 수 없습니다. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 상태 또는 멱등성 키가 요청과 충돌합니다. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description If-Match version이 현재 리소스와 다릅니다. */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 요청 값 또는 완료 조건이 유효하지 않습니다. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
            /** @description 현재 구성되지 않은 실행 정책이 필요합니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorResponse"];
                };
            };
        };
    };
    health_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 실행 중인 API 프로세스 상태 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
    readiness_health_ready_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description PostgreSQL 연결 가능 상태 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description PostgreSQL에 연결할 수 없습니다. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
}
