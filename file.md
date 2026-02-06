flowchart TD
  User --> App

  App --> Upload
  Upload --> Parse
  Parse --> Normalize
  Normalize --> SaveFileMeta
  Normalize --> SaveRows
  Normalize --> SaveEmbeddings

  SaveFileMeta --> MongoFiles
  SaveRows --> MongoRows
  SaveEmbeddings --> Chroma

  App --> Ask
  Ask --> Orchestrator

  Orchestrator --> QueryNormalizer
  QueryNormalizer --> RouterSchemaCheck

  RouterSchemaCheck -->|Schema query| SchemaGet
  SchemaGet --> SchemaAnswer
  SchemaAnswer --> App

  RouterSchemaCheck -->|Data or Explain| LatestSchema
  LatestSchema --> SemanticResolver

  SemanticResolver -->|Needs clarification| ClarificationMsg
  ClarificationMsg --> App

  SemanticResolver -->|OK| Planner
  Planner --> PolicyGuard

  PolicyGuard -->|Block or Clarify| App
  PolicyGuard -->|Allow| Fixes

  Fixes --> MergeResolution
  MergeResolution --> RouterType

  RouterType --> DataAgent
  DataAgent --> MongoFindRows
  DataAgent --> AggregationCache
  DataAgent -->|Explain or Summarize| ChromaQuery

  DataAgent -->|No rows| NoDataExplain
  NoDataExplain --> App

  DataAgent -->|Has rows| Analyst
  Analyst --> Responder
  Responder --> App
